package com.scd.dbmanager;

import com.google.gson.Gson;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.*;
import java.util.*;

/**
 * Encapsula a conexão JDBC com o arquivo SQLite de um shard principal.
 * Cada instância representa exatamente um arquivo .db.
 */
public class ShardDatabase {

    private static final Logger log = LoggerFactory.getLogger(ShardDatabase.class);
    private static final Gson GSON = new Gson();

    private final String shardId;
    private final String dbPath;
    private Connection conn;

    public ShardDatabase(String shardId, String dbPath) {
        this.shardId = shardId;
        this.dbPath = dbPath;
    }

    // ── lifecycle ────────────────────────────────────────────────────────────

    public synchronized void open() throws SQLException {
        conn = DriverManager.getConnection("jdbc:sqlite:" + dbPath);
        try (Statement st = conn.createStatement()) {
            st.execute("PRAGMA journal_mode=WAL");
            st.execute("PRAGMA foreign_keys=ON");
        }
        log.info("[{}] Banco aberto: {}", shardId, dbPath);
    }

    public synchronized void initSchema(String seedSql) throws SQLException {
        assertOpen();

        // Remove comentários de linha (-- ...) antes do split, para evitar
        // statements vazios ou compostos só por comentário após o split(";").
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
                    "Falha ao executar statement do seed: " + trimmed.substring(0, Math.min(80, trimmed.length()))
                    + "... — " + e.getMessage(), e);
            }
        }
        log.info("[{}] Schema inicializado", shardId);
    }

    public synchronized void close() {
        if (conn != null) {
            try { conn.close(); } catch (SQLException ignored) {}
            conn = null;
        }
    }

    public boolean isOpen() {
        try { return conn != null && !conn.isClosed(); } catch (SQLException e) { return false; }
    }

    // ── read ─────────────────────────────────────────────────────────────────

    /**
     * Executa um SELECT e retorna cada linha como JSON serializado.
     */
    public synchronized List<String> read(String sql, List<String> params) throws SQLException {
        assertOpen();
        List<String> rows = new ArrayList<>();
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                ResultSetMetaData meta = rs.getMetaData();
                int cols = meta.getColumnCount();
                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    for (int i = 1; i <= cols; i++) {
                        row.put(meta.getColumnLabel(i), rs.getObject(i));
                    }
                    rows.add(GSON.toJson(row));
                }
            }
        }
        return rows;
    }

    // ── write ────────────────────────────────────────────────────────────────

    /**
     * Executa uma DML (INSERT / UPDATE / DELETE).
     * Retorna o número de linhas afetadas.
     */
    public synchronized int write(String sql, List<String> params) throws SQLException {
        assertOpen();
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            bind(ps, params);
            return ps.executeUpdate();
        }
    }

    // ── idempotência ─────────────────────────────────────────────────────────

    public synchronized boolean isProcessed(String originId) throws SQLException {
        assertOpen();
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT 1 FROM processed_writes WHERE origin_id = ?")) {
            ps.setString(1, originId);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next();
            }
        }
    }

    public synchronized void markProcessed(String originId) throws SQLException {
        assertOpen();
        try (PreparedStatement ps = conn.prepareStatement(
                "INSERT OR IGNORE INTO processed_writes (origin_id, created_at) VALUES (?, datetime('now'))")) {
            ps.setString(1, originId);
            ps.executeUpdate();
        }
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    private void assertOpen() {
        if (!isOpen()) throw new IllegalStateException("Banco " + shardId + " não está aberto");
    }

    private void bind(PreparedStatement ps, List<String> params) throws SQLException {
        if (params != null) {
            for (int i = 0; i < params.size(); i++) {
                ps.setString(i + 1, params.get(i));
            }
        }
    }

    public String getShardId() { return shardId; }
    public String getDbPath()  { return dbPath;  }
}
