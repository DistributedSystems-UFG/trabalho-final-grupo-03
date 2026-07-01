package com.scd.replica;

/**
 * Deriva a porta de um ReplicaAgent a partir de
 * gerente_bd.replicas_porta_base e do índice (shard, réplica).
 *
 * Mesma convenção usada pelo Gerente de BD (ver
 * com.scd.dbmanager.ReplicaTopology) — fonte única de verdade
 * sobre portas, compartilhada via config.yaml.
 *
 *   porta(shard, replicaIndex) = replicas_porta_base
 *                                 + (shardIndex * 100)
 *                                 + replicaIndex
 *
 *   shard_a → shardIndex 0 → portas base+0,   base+1,   ...
 *   shard_b → shardIndex 1 → portas base+100, base+101, ...
 *   shard_c → shardIndex 2 → portas base+200, base+201, ...
 */
public class ReplicaTopology {

    private static final int SHARD_PORT_SPACING = 100;

    private final int basePort;
    private final int replicasPerShard;

    public ReplicaTopology(int basePort, int replicasPerShard) {
        this.basePort = basePort;
        this.replicasPerShard = replicasPerShard;
    }

    /** Índice 0-based do shard (shard_a=0, shard_b=1, shard_c=2). */
    public static int shardIndex(String shardId) {
        switch (shardId) {
            case "shard_a": return 0;
            case "shard_b": return 1;
            case "shard_c": return 2;
            default:
                throw new IllegalArgumentException("shard_id desconhecido: " + shardId
                    + " (esperado: shard_a, shard_b ou shard_c)");
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

    public int replicasPerShard() {
        return replicasPerShard;
    }
}
