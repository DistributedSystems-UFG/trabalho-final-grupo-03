package com.scd.dbmanager;

import com.scd.replica.proto.ReplicaAgentGrpc;
import com.scd.replica.proto.WriteAck;
import com.scd.replica.proto.WriteRequest;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.StatusRuntimeException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;
import java.util.concurrent.*;

/**
 * Gerencia a replicação assíncrona de writes para os ReplicaAgents remotos.
 *
 * Responsabilidades:
 *  - Manter canais gRPC para cada ReplicaAgent de cada shard
 *  - Disparar ApplyWrite em paralelo (best-effort com retry)
 *  - Registrar writes pendentes para reaplicação após falha
 */
public class ReplicationManager {

    private static final Logger log = LoggerFactory.getLogger(ReplicationManager.class);
    private static final int RETRY_ATTEMPTS = 3;
    private static final long RETRY_DELAY_MS = 500;

    /** shard_id → lista de stubs para cada ReplicaAgent */
    private final Map<String, List<ReplicaStub>> stubs = new HashMap<>();

    /**
     * Fila de writes pendentes por shard_id → endereço da réplica.
     * Usada para replay após reconexão de uma réplica.
     */
    private final Map<String, Queue<PendingWrite>> pendingWrites = new ConcurrentHashMap<>();

    private final ExecutorService executor = Executors.newCachedThreadPool(r -> {
        Thread t = new Thread(r, "replication-worker");
        t.setDaemon(true);
        return t;
    });

    // ── inicialização ────────────────────────────────────────────────────────

    public void init(ConfigLoader config) {
        for (String shardId : ShardRouter.ALL_SHARDS) {
            List<ReplicaStub> shardStubs = new ArrayList<>();
            for (ConfigLoader.ReplicaAddress addr : config.getReplicaAddresses(shardId)) {
                ManagedChannel channel = ManagedChannelBuilder
                        .forAddress(addr.host, addr.port)
                        .usePlaintext()
                        .build();
                ReplicaAgentGrpc.ReplicaAgentBlockingStub stub =
                        ReplicaAgentGrpc.newBlockingStub(channel);
                shardStubs.add(new ReplicaStub(addr.id(), channel, stub));
                log.info("Réplica registrada: {} → {}", shardId, addr.id());
            }
            stubs.put(shardId, shardStubs);
            pendingWrites.put(shardId, new ConcurrentLinkedQueue<>());
        }
    }

    public void shutdown() {
        executor.shutdown();
        stubs.values().stream()
             .flatMap(List::stream)
             .forEach(s -> s.channel.shutdown());
    }

    // ── replicação ───────────────────────────────────────────────────────────

    /**
     * Dispara ApplyWrite assíncrono em todas as réplicas do shard.
     * Writes com falha ficam na fila pendingWrites para retry/replay.
     */
    public void replicateAsync(String shardId, String sql, List<String> params, String originId) {
        List<ReplicaStub> shardStubs = stubs.getOrDefault(shardId, Collections.emptyList());
        WriteRequest req = WriteRequest.newBuilder()
                .setSql(sql)
                .addAllParams(params != null ? params : List.of())
                .setOriginId(originId)
                .build();

        for (ReplicaStub stub : shardStubs) {
            executor.submit(() -> applyWithRetry(shardId, stub, req));
        }
    }

    private void applyWithRetry(String shardId, ReplicaStub stub, WriteRequest req) {
        for (int attempt = 1; attempt <= RETRY_ATTEMPTS; attempt++) {
            try {
                WriteAck ack = stub.blockingStub.applyWrite(req);
                if (ack.getSuccess()) {
                    flushPending(shardId, stub);
                    return;
                }
                log.warn("[{}] Réplica {} recusou write ({}): {}",
                        shardId, stub.id, attempt, ack.getError());
            } catch (StatusRuntimeException e) {
                log.warn("[{}] Réplica {} inacessível (tentativa {}/{}): {}",
                        shardId, stub.id, attempt, RETRY_ATTEMPTS, e.getStatus());
            }
            try { Thread.sleep(RETRY_DELAY_MS * attempt); } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        // Todas as tentativas falharam — enfileira para replay
        pendingWrites.get(shardId).add(new PendingWrite(stub.id, req));
        log.error("[{}] Write enfileirado para replay na réplica {}", shardId, stub.id);
    }

    /**
     * Tenta aplicar writes pendentes para uma réplica que voltou a responder.
     */
    private void flushPending(String shardId, ReplicaStub stub) {
        Queue<PendingWrite> queue = pendingWrites.get(shardId);
        if (queue == null || queue.isEmpty()) return;

        List<PendingWrite> toRetry = new ArrayList<>();
        PendingWrite pw;
        while ((pw = queue.poll()) != null) {
            if (!pw.replicaId.equals(stub.id)) {
                toRetry.add(pw);
                continue;
            }
            try {
                WriteAck ack = stub.blockingStub.applyWrite(pw.request);
                if (!ack.getSuccess()) {
                    toRetry.add(pw);
                    log.warn("[{}] Replay falhou na réplica {}: {}", shardId, stub.id, ack.getError());
                } else {
                    log.info("[{}] Replay aplicado na réplica {}: origin_id={}",
                            shardId, stub.id, pw.request.getOriginId());
                }
            } catch (Exception e) {
                toRetry.add(pw);
                log.warn("[{}] Replay erro na réplica {}: {}", shardId, stub.id, e.getMessage());
            }
        }
        queue.addAll(toRetry);
    }

    /**
     * Replica escrita em tabelas globais para todos os shards.
     * FIRE-AND-FORGET: dispara a replicação em background e retorna
     * imediatamente — réplicas existem apenas para failover e NUNCA
     * devem atrasar o WriteAck retornado ao cliente. Falhas de réplica
     * são logadas e enfileiradas para retry/replay, igual aos writes
     * particionados (replicateAsync).
     */
    public void replicateGlobalAsync(String sql, List<String> params, String originId) {
        WriteRequest req = WriteRequest.newBuilder()
                .setSql(sql)
                .addAllParams(params != null ? params : List.of())
                .setOriginId(originId)
                .build();

        for (Map.Entry<String, List<ReplicaStub>> entry : stubs.entrySet()) {
            String shardId = entry.getKey();
            for (ReplicaStub stub : entry.getValue()) {
                executor.submit(() -> applyWithRetry(shardId, stub, req));
            }
        }
    }

    // ── estado das réplicas ───────────────────────────────────────────────────

    public int countOnlineReplicas(String shardId) {
        return (int) stubs.getOrDefault(shardId, List.of()).stream()
                .filter(s -> pingStub(s) )
                .count();
    }

    public int countTotalReplicas(String shardId) {
        return stubs.getOrDefault(shardId, List.of()).size();
    }

    public List<String> getReplicaIds(String shardId) {
        return stubs.getOrDefault(shardId, List.of()).stream()
                .map(s -> s.id)
                .toList();
    }

    private boolean pingStub(ReplicaStub stub) {
        try {
            com.scd.replica.proto.PingResponse r =
                    stub.blockingStub.ping(com.scd.replica.proto.PingRequest.newBuilder().build());
            return r.getOk();
        } catch (Exception e) {
            return false;
        }
    }

    // ── inner types ──────────────────────────────────────────────────────────

    private static class ReplicaStub {
        final String id;
        final ManagedChannel channel;
        final ReplicaAgentGrpc.ReplicaAgentBlockingStub blockingStub;

        ReplicaStub(String id, ManagedChannel channel,
                    ReplicaAgentGrpc.ReplicaAgentBlockingStub blockingStub) {
            this.id = id;
            this.channel = channel;
            this.blockingStub = blockingStub;
        }
    }

    private static class PendingWrite {
        final String replicaId;
        final WriteRequest request;

        PendingWrite(String replicaId, WriteRequest request) {
            this.replicaId = replicaId;
            this.request = request;
        }
    }
}
