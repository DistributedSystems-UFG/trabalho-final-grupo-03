package com.scd.replica;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.yaml.snakeyaml.Yaml;

import java.io.*;
import java.nio.file.*;
import java.util.Map;

/**
 * Carrega config.yaml do diretório de trabalho (mesmo arquivo compartilhado
 * por todos os componentes, na raiz do projeto).
 * Se não existir, copia o config.template.yaml embutido no JAR.
 *
 * O ReplicaAgent é identificado por --shard <shard_id> --index <N> na linha
 * de comando (N = índice 0-based da réplica dentro do shard). A porta é
 * DERIVADA automaticamente via ReplicaTopology a partir de
 * gerente_bd.replicas_porta_base e gerente_bd.qtd_max_replicas — o usuário
 * nunca escreve portas de réplica no config.yaml.
 */
public class ConfigLoader {

    private static final Logger log = LoggerFactory.getLogger(ConfigLoader.class);
    private static final String CONFIG_FILE = "config.yaml";
    private static final String TEMPLATE_RESOURCE = "/config.template.yaml";

    private final Map<String, Object> root;

    public ConfigLoader() {
        bootstrap();
        try (InputStream in = new FileInputStream(CONFIG_FILE)) {
            this.root = new Yaml().load(in);
        } catch (IOException e) {
            throw new RuntimeException("Falha ao carregar " + CONFIG_FILE, e);
        }
    }

    // ── bootstrap ────────────────────────────────────────────────────────────

    private void bootstrap() {
        Path cfg = Paths.get(CONFIG_FILE);
        if (Files.exists(cfg)) return;

        log.warn("config.yaml não encontrado — criado a partir do template padrão");
        try (InputStream tpl = ConfigLoader.class.getResourceAsStream(TEMPLATE_RESOURCE)) {
            if (tpl == null)
                throw new RuntimeException("config.template.yaml não encontrado no JAR");
            Files.copy(tpl, cfg);
        } catch (IOException e) {
            throw new RuntimeException("Falha ao criar config.yaml a partir do template", e);
        }
    }

    // ── accessors ────────────────────────────────────────────────────────────

    public String getDataDir() {
        return getString("dados.diretorio", "./data");
    }

    public int getMaxReplicas() {
        return getInt("gerente_bd.qtd_max_replicas", 2);
    }

    public int getReplicasPortaBase() {
        return getInt("gerente_bd.replicas_porta_base", 50100);
    }

    /**
     * Constrói a topologia de portas — mesma fórmula usada pelo Gerente de BD.
     */
    public ReplicaTopology getReplicaTopology() {
        return new ReplicaTopology(getReplicasPortaBase(), getMaxReplicas());
    }

    /**
     * Calcula a porta desta instância e valida que o índice está dentro
     * de [0, qtd_max_replicas). Lança exceção com mensagem clara se não estiver.
     */
    public int resolvePort(String shardId, int replicaIndex) {
        ReplicaTopology topology = getReplicaTopology();
        if (replicaIndex < 0 || replicaIndex >= topology.replicasPerShard()) {
            throw new IllegalArgumentException(
                "--index " + replicaIndex + " fora do intervalo [0, "
                + (topology.replicasPerShard() - 1) + "] — "
                + "gerente_bd.qtd_max_replicas=" + topology.replicasPerShard()
                + " no config.yaml");
        }
        return topology.portFor(shardId, replicaIndex);
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    @SuppressWarnings("unchecked")
    private String getString(String dotPath, String defaultVal) {
        Object val = traverse(dotPath);
        return val != null ? val.toString() : defaultVal;
    }

    @SuppressWarnings("unchecked")
    private int getInt(String dotPath, int defaultVal) {
        Object val = traverse(dotPath);
        return val instanceof Number ? ((Number) val).intValue() : defaultVal;
    }

    @SuppressWarnings("unchecked")
    private Object traverse(String dotPath) {
        String[] parts = dotPath.split("\\.");
        Object cur = root;
        for (String part : parts) {
            if (!(cur instanceof Map)) return null;
            cur = ((Map<String, Object>) cur).get(part);
        }
        return cur;
    }
}
