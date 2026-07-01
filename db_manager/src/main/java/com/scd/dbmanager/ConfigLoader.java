package com.scd.dbmanager;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.yaml.snakeyaml.Yaml;

import java.io.*;
import java.nio.file.*;
import java.util.*;

/**
 * Carrega config.yaml do diretório de trabalho.
 * Se o arquivo não existir, copia config.template.yaml embutido no JAR.
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

    public String getDbManagerHost() {
        return getString("gerente_bd.host", "0.0.0.0");
    }

    public int getDbManagerPort() {
        return getInt("gerente_bd.porta", 50050);
    }

    public int getMaxReplicas() {
        return getInt("gerente_bd.qtd_max_replicas", 2);
    }

    public String getReplicasHost() {
        return getString("gerente_bd.replicas_host", "localhost");
    }

    public int getReplicasPortaBase() {
        return getInt("gerente_bd.replicas_porta_base", 50100);
    }

    public String getDataDir() {
        return getString("dados.diretorio", "./data");
    }

    /**
     * Constrói a topologia de réplicas derivada de
     * qtd_max_replicas + replicas_host + replicas_porta_base.
     * Fonte única de verdade — usada também pelo ReplicaAgent.
     */
    public ReplicaTopology getReplicaTopology() {
        return new ReplicaTopology(getReplicasHost(), getReplicasPortaBase(), getMaxReplicas());
    }

    /**
     * Retorna lista de endereços { host, porta } para cada ReplicaAgent de um shard,
     * derivados automaticamente pela ReplicaTopology.
     */
    public List<ReplicaAddress> getReplicaAddresses(String shardId) {
        return getReplicaTopology().addressesFor(shardId);
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

    // ── inner type ───────────────────────────────────────────────────────────

    public static class ReplicaAddress {
        public final String host;
        public final int port;

        public ReplicaAddress(String host, int port) {
            this.host = host;
            this.port = port;
        }

        public String id() {
            return host + ":" + port;
        }

        @Override
        public String toString() {
            return id();
        }
    }
}
