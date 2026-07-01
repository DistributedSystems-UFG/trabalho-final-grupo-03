package com.scd.replica;

import com.scd.replica.proto.*;
import io.grpc.Server;
import io.grpc.ServerBuilder;
import io.grpc.stub.StreamObserver;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.*;
import java.sql.*;
import java.util.List;
import java.util.concurrent.locks.ReentrantLock;

/**
 * Processo Java leve que serve como réplica remota de um shard SQLite.
 *
 * Expõe dois RPCs via gRPC (porta configurável):
 *   - ApplyWrite  — recebe um WriteRequest e o aplica no SQLite local
 *   - Ping        — healthcheck; retorna ok + caminho do .db
 *
 * Sem lógica de negócio, sem ShardRouter, sem locks distribuídos.
 * Apenas persiste o que o Gerente de BD enviar.
 *
 * Uso:
 *   java -jar replica-agent.jar --shard <shard_id> --index <N>
 *   (porta derivada automaticamente — ver ReplicaTopology)
 */
public class ReplicaAgentService extends ReplicaAgentGrpc.ReplicaAgentImplBase {

    private static final Logger log = LoggerFactory.getLogger(ReplicaAgentService.class);

    private final String shardId;
    private final String dbPath;
    private final Connection conn;

    // Lock local — serializa escritas concorrentes no mesmo arquivo .db
    private final ReentrantLock writeLock = new ReentrantLock();

    public ReplicaAgentService(String shardId, String dbPath) throws SQLException {
        this.shardId = shardId;
        this.dbPath  = dbPath;
        this.conn    = openConnection(dbPath);
        initSchema();
        log.info("[{}] ReplicaAgent pronto — db: {}", shardId, dbPath);
    }

    // ── gRPC: ApplyWrite ──────────────────────────────────────────────────────

    @Override
    public void applyWrite(WriteRequest request, StreamObserver<WriteAck> responseObserver) {
        writeLock.lock();
        try {
            // Idempotência: se origin_id já foi processado, ignora silenciosamente
            if (!request.getOriginId().isBlank() && isProcessed(request.getOriginId())) {
                log.debug("[{}] origin_id duplicado ignorado: {}", shardId, request.getOriginId());
                responseObserver.onNext(WriteAck.newBuilder().setSuccess(true).build());
                responseObserver.onCompleted();
                return;
            }

            executeWrite(request.getSql(), request.getParamsList());

            if (!request.getOriginId().isBlank()) {
                markProcessed(request.getOriginId());
            }

            log.debug("[{}] Write aplicado — origin_id: {}", shardId, request.getOriginId());
            responseObserver.onNext(WriteAck.newBuilder().setSuccess(true).build());

        } catch (SQLException e) {
            log.error("[{}] Falha ao aplicar write: {}", shardId, e.getMessage());
            responseObserver.onNext(WriteAck.newBuilder()
                    .setSuccess(false)
                    .setError(e.getMessage())
                    .build());
        } finally {
            writeLock.unlock();
            responseObserver.onCompleted();
        }
    }

    // ── gRPC: Ping ────────────────────────────────────────────────────────────

    @Override
    public void ping(PingRequest request, StreamObserver<PingResponse> responseObserver) {
        boolean ok = isDbAccessible();
        responseObserver.onNext(PingResponse.newBuilder()
                .setOk(ok)
                .setDbPath(dbPath)
                .build());
        responseObserver.onCompleted();
    }

    // ── SQL helpers ───────────────────────────────────────────────────────────

    private void executeWrite(String sql, List<String> params) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            for (int i = 0; i < params.size(); i++) {
                ps.setString(i + 1, params.get(i));
            }
            ps.executeUpdate();
        }
    }

    private boolean isProcessed(String originId) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT 1 FROM processed_writes WHERE origin_id = ?")) {
            ps.setString(1, originId);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next();
            }
        }
    }

    private void markProcessed(String originId) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "INSERT OR IGNORE INTO processed_writes (origin_id, created_at) VALUES (?, datetime('now'))")) {
            ps.setString(1, originId);
            ps.executeUpdate();
        }
    }

    private boolean isDbAccessible() {
        try (Statement st = conn.createStatement()) {
            st.execute("SELECT 1");
            return true;
        } catch (SQLException e) {
            return false;
        }
    }

    // ── inicialização ─────────────────────────────────────────────────────────

    private Connection openConnection(String path) throws SQLException {
        Connection c = DriverManager.getConnection("jdbc:sqlite:" + path);
        try (Statement st = c.createStatement()) {
            st.execute("PRAGMA journal_mode=WAL");
            st.execute("PRAGMA foreign_keys=ON");
        }
        return c;
    }

    /**
     * Executa o seed.sql completo — cria a mesma ESTRUTURA de tabelas
     * que o principal. Os DADOS chegam via replicação (ApplyWrite),
     * não deste arquivo. O INSERT do admin é idempotente (OR IGNORE).
     */
    private void initSchema() throws SQLException {
        String seedSql = loadSeed();

        // Remove comentários de linha (-- ...) antes do split, evitando
        // statements vazios ou compostos só por comentário após split(";").
        String cleaned = seedSql.lines()
                .map(line -> {
                    int idx = line.indexOf("--");
                    return idx >= 0 ? line.substring(0, idx) : line;
                })
                .reduce("", (acc, line) -> acc + line + "\n");

        for (String stmt : cleaned.split(";")) {
            String trimmed = stmt.trim();
            if (trimmed.isEmpty()) continue;

            try (Statement st = conn.createStatement()) {
                st.execute(trimmed);
            } catch (SQLException e) {
                throw new SQLException(
                    "Falha ao executar statement do seed: "
                    + trimmed.substring(0, Math.min(80, trimmed.length()))
                    + "... — " + e.getMessage(), e);
            }
        }
    }

    private String loadSeed() {
        try (InputStream in = getClass().getResourceAsStream("/seed.sql")) {
            if (in == null) throw new RuntimeException("seed.sql não encontrado no classpath");
            return new String(in.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new RuntimeException("Falha ao carregar seed.sql", e);
        }
    }

    public void close() {
        try { conn.close(); } catch (SQLException ignored) {}
    }

    // ── main ──────────────────────────────────────────────────────────────────

    public static void main(String[] args) throws IOException, InterruptedException, SQLException {
        // Carrega config com bootstrap automático
        ConfigLoader config = new ConfigLoader();

        // --shard e --index identificam esta instância.
        // index = posição 0-based da réplica dentro do shard (0, 1, 2, ...)
        String shardId = null;
        Integer index   = null;

        for (int i = 0; i < args.length - 1; i++) {
            if ("--shard".equals(args[i])) shardId = args[i + 1];
            if ("--index".equals(args[i])) index   = Integer.parseInt(args[i + 1]);
        }

        if (shardId == null || index == null) {
            System.err.println("Uso: java -jar replica-agent.jar --shard <shard_id> --index <N>");
            System.err.println("  shard_id: shard_a | shard_b | shard_c");
            System.err.println("  N: índice 0-based da réplica (0..qtd_max_replicas-1)");
            System.exit(1);
            return;
        }

        // Porta derivada automaticamente — mesma fórmula usada pelo Gerente de BD
        final int port = config.resolvePort(shardId, index);

        // Garante que o diretório do shard existe
        Path dbDir = Paths.get(config.getDataDir(), shardId);
        Files.createDirectories(dbDir);
        String dbPath = dbDir.resolve("replica_" + index + ".db").toString();

        final ReplicaAgentService service = new ReplicaAgentService(shardId, dbPath);
        final String finalShardId = shardId;
        final int finalIndex = index;

        final Server server = ServerBuilder.forPort(port)
                .addService(service)
                .build()
                .start();

        log.info("ReplicaAgent [{}#{}] escutando na porta {} — db: {}",
                shardId, finalIndex, port, dbPath);

        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            log.info("Encerrando ReplicaAgent [{}#{}]...", finalShardId, finalIndex);
            server.shutdown();
            service.close();
        }));

        server.awaitTermination();
    }
}
