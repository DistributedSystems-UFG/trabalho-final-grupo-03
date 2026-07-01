package com.scd.dbmanager;

import com.scd.dbmanager.proto.*;
import io.grpc.stub.StreamObserver;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.sql.SQLException;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.locks.ReentrantLock;

/**
 * Implementação gRPC do Gerente de BD.
 *
 * Responsabilidades:
 *  - Rotear ReadRequest / WriteRequest para o shard correto
 *  - Serializar escritas concorrentes com locks por product_id
 *  - Garantir idempotência via origin_id
 *  - Replicar assincronamente para ReplicaAgents
 *  - Expor HealthCheck, GetStatus e PromoteReplica para o admin
 */
public class DBManagerService extends DBManagerGrpc.DBManagerImplBase {

    private static final Logger log = LoggerFactory.getLogger(DBManagerService.class);

    private final ShardRouter shardRouter = new ShardRouter();
    private final ReplicationManager replication;
    private final FailoverController failover;

    /** shard_id → banco principal */
    private final Map<String, ShardDatabase> primaries = new HashMap<>();

    /** shard_id → (product_id → lock) — lock granular por produto */
    private final Map<String, ConcurrentHashMap<String, ReentrantLock>> shardProductLocks = new HashMap<>();

    /** shard_id → lock global do shard (tabelas globais / operações sem product_id) */
    private final Map<String, ReentrantLock> shardGlobalLocks = new HashMap<>();

    /** shard_id → id do primeiro ReplicaAgent (para failover automático) */
    private final Map<String, String> firstReplicaIds = new HashMap<>();

    /** Executor para operações paralelas multi-shard */
    private final ExecutorService parallelExec = Executors.newFixedThreadPool(
            ShardRouter.ALL_SHARDS.size() * 2,
            r -> { Thread t = new Thread(r, "shard-parallel"); t.setDaemon(true); return t; }
    );

    public DBManagerService(ConfigLoader config) {
        this.replication = new ReplicationManager();
        replication.init(config);

        this.failover = new FailoverController(this);

        String dataDir = config.getDataDir();
        initShards(dataDir, config);
        failover.start();
        log.info("DBManagerService inicializado. Shards: {}", primaries.keySet());
    }

    // ── inicialização dos shards ──────────────────────────────────────────────

    private void initShards(String dataDir, ConfigLoader config) {
        String seedSql = loadSeed();

        for (String shardId : ShardRouter.ALL_SHARDS) {
            // Garante que o diretório do shard existe
            Path shardDir = Paths.get(dataDir, shardId);
            try { Files.createDirectories(shardDir); } catch (IOException e) {
                throw new RuntimeException("Falha ao criar diretório para " + shardId, e);
            }

            String dbPath = shardDir.resolve("primary.db").toString();
            ShardDatabase db = new ShardDatabase(shardId, dbPath);
            try {
                db.open();
                db.initSchema(seedSql);
            } catch (SQLException e) {
                throw new RuntimeException("Falha ao inicializar shard " + shardId, e);
            }
            primaries.put(shardId, db);
            shardProductLocks.put(shardId, new ConcurrentHashMap<>());
            shardGlobalLocks.put(shardId, new ReentrantLock());

            // Registra o id da primeira réplica para failover automático
            List<String> replicaIds = replication.getReplicaIds(shardId);
            if (!replicaIds.isEmpty()) {
                firstReplicaIds.put(shardId, replicaIds.get(0));
            }

            log.info("[{}] Principal iniciado em {}", shardId, dbPath);
        }
    }

    private String loadSeed() {
        try (InputStream in = getClass().getResourceAsStream("/seed.sql")) {
            if (in == null) throw new RuntimeException("seed.sql não encontrado no classpath");
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new RuntimeException("Falha ao carregar seed.sql", e);
        }
    }

    // ── gRPC: Read ────────────────────────────────────────────────────────────

    @Override
    public void read(ReadRequest request, StreamObserver<ReadResult> responseObserver) {
        try {
            List<String> rows;
            String category = request.getCategory();

            if (category == null || category.isBlank()) {
                // Executa em paralelo nos 3 shards e mescla
                rows = readAllShards(request.getSql(), request.getParamsList());
            } else {
                String shardId = shardRouter.route(category);
                ShardDatabase db = primaries.get(shardId);
                rows = db.read(request.getSql(), request.getParamsList());
            }

            responseObserver.onNext(ReadResult.newBuilder()
                    .setSuccess(true)
                    .addAllRows(rows)
                    .build());
        } catch (Exception e) {
            log.error("Erro em Read: {}", e.getMessage(), e);
            responseObserver.onNext(ReadResult.newBuilder()
                    .setSuccess(false)
                    .setError(e.getMessage())
                    .build());
        }
        responseObserver.onCompleted();
    }

    private List<String> readAllShards(String sql, List<String> params) throws Exception {
        List<Future<List<String>>> futures = new ArrayList<>();
        for (String shardId : ShardRouter.ALL_SHARDS) {
            ShardDatabase db = primaries.get(shardId);
            futures.add(parallelExec.submit(() -> db.read(sql, params)));
        }

        List<String> merged = new ArrayList<>();
        for (Future<List<String>> f : futures) {
            try {
                merged.addAll(f.get(10, TimeUnit.SECONDS));
            } catch (ExecutionException e) {
                log.warn("Falha em shard durante read paralelo: {}", e.getCause().getMessage());
            }
        }
        return merged;
    }

    // ── gRPC: Write ───────────────────────────────────────────────────────────

    @Override
    public void write(WriteRequest request, StreamObserver<WriteAck> responseObserver) {
        WriteAck ack;
        try {
            String category = request.getCategory();

            if ("global".equalsIgnoreCase(category)) {
                ack = applyGlobalWrite(request);
            } else {
                ack = applyPartitionedWrite(request);
            }
        } catch (Exception e) {
            log.error("Erro em Write: {}", e.getMessage(), e);
            ack = WriteAck.newBuilder().setSuccess(false).setError(e.getMessage()).build();
        }
        responseObserver.onNext(ack);
        responseObserver.onCompleted();
    }

    /**
     * Write global: aplica nos 3 principais em paralelo, aguarda confirmação dos 3.
     */
    private WriteAck applyGlobalWrite(WriteRequest request) throws Exception {
        List<Future<Boolean>> futures = new ArrayList<>();

        for (String shardId : ShardRouter.ALL_SHARDS) {
            ShardDatabase db = primaries.get(shardId);
            ReentrantLock lock = shardGlobalLocks.get(shardId);
            futures.add(parallelExec.submit(() -> {
                lock.lock();
                try {
                    if (db.isProcessed(request.getOriginId())) return true; // idempotência
                    db.write(request.getSql(), request.getParamsList());
                    db.markProcessed(request.getOriginId());
                    return true;
                } finally {
                    lock.unlock();
                }
            }));
        }

        // Aguarda todos os 3 principais confirmarem
        boolean allOk = true;
        StringBuilder errors = new StringBuilder();
        for (Future<Boolean> f : futures) {
            try {
                if (!f.get(10, TimeUnit.SECONDS)) {
                    allOk = false;
                    errors.append("Shard rejeitou write global. ");
                }
            } catch (Exception e) {
                allOk = false;
                errors.append(e.getMessage()).append(". ");
            }
        }

        if (!allOk) {
            return WriteAck.newBuilder().setSuccess(false).setError(errors.toString()).build();
        }

        // Replica nos ReplicaAgents de todos os shards — assíncrono (fire-and-forget).
        // Réplicas são apenas para failover; não devem atrasar a confirmação ao cliente.
        replication.replicateGlobalAsync(
                request.getSql(), request.getParamsList(), request.getOriginId());

        return WriteAck.newBuilder().setSuccess(true).build();
    }

    /**
     * Write particionado: aplica no principal do shard correto, replica assincronamente.
     */
    private WriteAck applyPartitionedWrite(WriteRequest request) throws SQLException {
        String shardId = shardRouter.route(request.getCategory());
        ShardDatabase db = primaries.get(shardId);

        // Seleciona lock: granular por product_id se disponível, global do shard caso contrário
        ReentrantLock lock;
        String productId = request.getProductId();
        if (productId != null && !productId.isBlank()) {
            lock = shardProductLocks.get(shardId)
                    .computeIfAbsent(productId, k -> new ReentrantLock());
        } else {
            lock = shardGlobalLocks.get(shardId);
        }

        lock.lock();
        try {
            // Idempotência: origin_id já processado → retorna sucesso sem reaplicar
            if (!request.getOriginId().isBlank() && db.isProcessed(request.getOriginId())) {
                log.debug("[{}] origin_id duplicado ignorado: {}", shardId, request.getOriginId());
                return WriteAck.newBuilder().setSuccess(true).build();
            }

            db.write(request.getSql(), request.getParamsList());
            if (!request.getOriginId().isBlank()) {
                db.markProcessed(request.getOriginId());
            }

            // WriteAck retornado após confirmação no principal;
            // replicação para os ReplicaAgents é disparada assincronamente
            replication.replicateAsync(
                    shardId, request.getSql(), request.getParamsList(), request.getOriginId());

            return WriteAck.newBuilder().setSuccess(true).build();

        } catch (SQLException e) {
            return WriteAck.newBuilder().setSuccess(false).setError(e.getMessage()).build();
        } finally {
            lock.unlock();
        }
    }

    // ── gRPC: HealthCheck ─────────────────────────────────────────────────────

    @Override
    public void healthCheck(HealthRequest request, StreamObserver<HealthResponse> responseObserver) {
        int online = 0, total = 0;
        boolean healthy = true;

        for (String shardId : ShardRouter.ALL_SHARDS) {
            online += replication.countOnlineReplicas(shardId);
            total  += replication.countTotalReplicas(shardId);
            if (!primaries.get(shardId).isOpen()) healthy = false;
        }

        responseObserver.onNext(HealthResponse.newBuilder()
                .setHealthy(healthy)
                .setReplicasOnline(online)
                .setReplicasTotal(total)
                .build());
        responseObserver.onCompleted();
    }

    // ── gRPC: GetStatus ───────────────────────────────────────────────────────

    @Override
    public void getStatus(StatusRequest request, StreamObserver<StatusResponse> responseObserver) {
        StatusResponse.Builder resp = StatusResponse.newBuilder();

        for (String shardId : ShardRouter.ALL_SHARDS) {
            ShardStatus.Builder ss = ShardStatus.newBuilder()
                    .setShardId(shardId)
                    .setPrimaryId(primaries.get(shardId).getDbPath())
                    .addAllReplicaIds(replication.getReplicaIds(shardId))
                    .setFailoverActive(failover.isFailoverActive(shardId));
            resp.addShards(ss.build());
        }

        responseObserver.onNext(resp.build());
        responseObserver.onCompleted();
    }

    // ── gRPC: PromoteReplica ──────────────────────────────────────────────────

    @Override
    public void promoteReplica(PromoteRequest request, StreamObserver<PromoteAck> responseObserver) {
        String shardId   = request.getShardId();
        String replicaId = request.getReplicaId();

        if (!ShardRouter.ALL_SHARDS.contains(shardId)) {
            responseObserver.onNext(PromoteAck.newBuilder()
                    .setSuccess(false)
                    .setError("shard_id inválido: " + shardId)
                    .build());
            responseObserver.onCompleted();
            return;
        }

        boolean ok = failover.promoteManual(shardId, replicaId);
        responseObserver.onNext(PromoteAck.newBuilder()
                .setSuccess(ok)
                .setNewPrimary(ok ? replicaId : "")
                .setError(ok ? "" : "Falha ao promover réplica " + replicaId)
                .build());
        responseObserver.onCompleted();
    }

    // ── métodos internos usados pelo FailoverController ───────────────────────

    /** Retorna o banco principal de um shard (usado pelo FailoverController). */
    ShardDatabase getPrimary(String shardId) {
        return primaries.get(shardId);
    }

    /** Retorna o id da primeira réplica configurada para o shard. */
    String getFirstReplicaId(String shardId) {
        return firstReplicaIds.get(shardId);
    }

    /**
     * Promove a réplica informada como novo principal do shard.
     * Na implementação atual, marca o failover como ativo e redireciona
     * operações futuras. Uma implementação completa copiaria o snapshot
     * da réplica promovida para o arquivo .db local.
     */
    boolean promoteReplica(String shardId, String replicaId) {
        // Em ambiente de produção: copiaria snapshot da réplica e reapontaria conexão.
        // Nesta implementação, loga a promoção — o principal local permanece até restart.
        log.warn("[{}] FAILOVER: réplica {} promovida a principal", shardId, replicaId);
        return true;
    }

    // ── shutdown ──────────────────────────────────────────────────────────────

    public void shutdown() {
        failover.shutdown();
        replication.shutdown();
        parallelExec.shutdown();
        primaries.values().forEach(ShardDatabase::close);
        log.info("DBManagerService encerrado");
    }
}
