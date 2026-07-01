package com.scd.dbmanager;

import com.scd.replica.proto.*;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.StatusRuntimeException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Map;
import java.util.concurrent.*;

/**
 * Monitora a saúde dos BDs principais via healthcheck periódico.
 * Quando um principal falha, promove a Réplica 1 e notifica o DBManagerService.
 *
 * O failover opera independentemente por shard — a queda do Shard B
 * não afeta os Shards A e C.
 */
public class FailoverController {

    private static final Logger log = LoggerFactory.getLogger(FailoverController.class);
    private static final int HEALTHCHECK_INTERVAL_MS = 5_000;
    private static final int PING_TIMEOUT_MS = 3_000;

    private final DBManagerService service;
    private final ScheduledExecutorService scheduler =
            Executors.newScheduledThreadPool(1, r -> {
                Thread t = new Thread(r, "failover-monitor");
                t.setDaemon(true);
                return t;
            });

    /** Estado de failover por shard: true = failover ativo (principal substituído) */
    private final Map<String, Boolean> failoverActive = new ConcurrentHashMap<>();

    public FailoverController(DBManagerService service) {
        this.service = service;
        ShardRouter.ALL_SHARDS.forEach(s -> failoverActive.put(s, false));
    }

    // ── lifecycle ────────────────────────────────────────────────────────────

    public void start() {
        scheduler.scheduleAtFixedRate(this::runHealthcheck,
                HEALTHCHECK_INTERVAL_MS, HEALTHCHECK_INTERVAL_MS, TimeUnit.MILLISECONDS);
        log.info("FailoverController iniciado (intervalo {}ms)", HEALTHCHECK_INTERVAL_MS);
    }

    public void shutdown() {
        scheduler.shutdown();
    }

    // ── healthcheck ──────────────────────────────────────────────────────────

    private void runHealthcheck() {
        for (String shardId : ShardRouter.ALL_SHARDS) {
            try {
                checkShard(shardId);
            } catch (Exception e) {
                log.error("[{}] Erro no healthcheck: {}", shardId, e.getMessage());
            }
        }
    }

    private void checkShard(String shardId) {
        ShardDatabase primary = service.getPrimary(shardId);

        // Testa se o principal local responde (verifica se a conexão está aberta)
        if (!primary.isOpen()) {
            if (!failoverActive.get(shardId)) {
                log.warn("[{}] Principal inacessível — iniciando failover", shardId);
                triggerFailover(shardId);
            }
            return;
        }

        // Se estava em failover e o original voltou, tenta restaurar
        if (failoverActive.get(shardId)) {
            log.info("[{}] Principal original parece ter voltado — verificando...", shardId);
            // O replay é iniciado pelo próprio serviço ao receber a notificação de volta
            // (fora do escopo desta verificação de healthcheck simples)
        }
    }

    /**
     * Promove a Réplica 1 do shard como novo principal.
     * Chamado tanto automaticamente (healthcheck) quanto manualmente (/admin/promote).
     */
    public synchronized boolean triggerFailover(String shardId) {
        if (failoverActive.get(shardId)) {
            log.warn("[{}] Failover já ativo, ignorando solicitação duplicada", shardId);
            return false;
        }

        String firstReplicaId = service.getFirstReplicaId(shardId);
        if (firstReplicaId == null) {
            log.error("[{}] Sem réplicas disponíveis para failover!", shardId);
            return false;
        }

        log.warn("[{}] Promovendo réplica {} como novo principal", shardId, firstReplicaId);
        boolean promoted = service.promoteReplica(shardId, firstReplicaId);
        if (promoted) {
            failoverActive.put(shardId, true);
            log.info("[{}] Failover concluído — novo principal: {}", shardId, firstReplicaId);
        }
        return promoted;
    }

    /**
     * Promove manualmente uma réplica específica (chamado via /admin/promote).
     */
    public synchronized boolean promoteManual(String shardId, String replicaId) {
        log.info("[{}] Promoção manual solicitada: réplica={}", shardId, replicaId);
        boolean promoted = service.promoteReplica(shardId, replicaId);
        if (promoted) {
            failoverActive.put(shardId, true);
        }
        return promoted;
    }

    public boolean isFailoverActive(String shardId) {
        return failoverActive.getOrDefault(shardId, false);
    }

    public void clearFailover(String shardId) {
        failoverActive.put(shardId, false);
        log.info("[{}] Estado de failover limpo", shardId);
    }
}
