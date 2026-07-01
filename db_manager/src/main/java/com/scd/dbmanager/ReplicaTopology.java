package com.scd.dbmanager;

import java.util.ArrayList;
import java.util.List;

/**
 * Deriva a topologia de ReplicaAgents automaticamente a partir de
 * gerente_bd.qtd_max_replicas, gerente_bd.replicas_host e
 * gerente_bd.replicas_porta_base — sem que o usuário precise listar
 * portas manualmente no config.yaml.
 *
 * Convenção de portas:
 *
 *   porta(shard, replicaIndex) = replicas_porta_base
 *                                 + (shardIndex * 100)
 *                                 + replicaIndex
 *
 *   shard_a → shardIndex 0 → portas base+0,   base+1,   ...
 *   shard_b → shardIndex 1 → portas base+100, base+101, ...
 *   shard_c → shardIndex 2 → portas base+200, base+201, ...
 *
 * Com o default replicas_porta_base=50100 e qtd_max_replicas=2:
 *   shard_a → 50100, 50101
 *   shard_b → 50200, 50201
 *   shard_c → 50300, 50301
 *
 * Esta é a ÚNICA fonte de verdade sobre portas de réplicas — tanto o
 * Gerente de BD quanto cada ReplicaAgent calculam a mesma topologia a
 * partir do mesmo config.yaml.
 */
public class ReplicaTopology {

    private static final int SHARD_PORT_SPACING = 100;

    private final String host;
    private final int basePort;
    private final int replicasPerShard;

    public ReplicaTopology(String host, int basePort, int replicasPerShard) {
        this.host = host;
        this.basePort = basePort;
        this.replicasPerShard = replicasPerShard;
    }

    /** Índice 0-based do shard (shard_a=0, shard_b=1, shard_c=2). */
    public static int shardIndex(String shardId) {
        switch (shardId) {
            case ShardRouter.SHARD_A: return 0;
            case ShardRouter.SHARD_B: return 1;
            case ShardRouter.SHARD_C: return 2;
            default:
                throw new IllegalArgumentException("shard_id desconhecido: " + shardId);
        }
    }

    /** Porta do ReplicaAgent de índice replicaIndex (0-based) no shard. */
    public int portFor(String shardId, int replicaIndex) {
        if (replicaIndex < 0 || replicaIndex >= replicasPerShard) {
            throw new IllegalArgumentException(
                "replicaIndex " + replicaIndex + " fora do intervalo [0, "
                + (replicasPerShard - 1) + "] para qtd_max_replicas=" + replicasPerShard);
        }
        return basePort + shardIndex(shardId) * SHARD_PORT_SPACING + replicaIndex;
    }

    public String host() {
        return host;
    }

    public int replicasPerShard() {
        return replicasPerShard;
    }

    /** Endereços (host:porta) de todas as réplicas de um shard, na ordem dos índices. */
    public List<ConfigLoader.ReplicaAddress> addressesFor(String shardId) {
        List<ConfigLoader.ReplicaAddress> result = new ArrayList<>();
        for (int i = 0; i < replicasPerShard; i++) {
            result.add(new ConfigLoader.ReplicaAddress(host, portFor(shardId, i)));
        }
        return result;
    }

    /** Identificador legível usado em logs e em GetStatus (ex.: "shard_a#0"). */
    public static String replicaId(String shardId, int replicaIndex) {
        return shardId + "#" + replicaIndex;
    }
}
