package com.scd.dbmanager;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;

/**
 * Mapeia uma categoria de produto para o shard correspondente.
 *
 * Shard A → Eletrônicos, Informática, Telefonia
 * Shard B → Roupas, Calçados, Acessórios
 * Shard C → Casa, Esporte, Outros (+ fallback para qualquer categoria desconhecida)
 */
public class ShardRouter {

    public static final String SHARD_A = "shard_a";
    public static final String SHARD_B = "shard_b";
    public static final String SHARD_C = "shard_c";

    public static final Set<String> ALL_SHARDS = Set.of(SHARD_A, SHARD_B, SHARD_C);

    private static final Map<String, String> CATEGORY_MAP = new HashMap<>();

    static {
        // Shard A
        for (String cat : new String[]{"Eletrônicos", "Eletronicos", "Informatica",
                "Informática", "Telefonia"}) {
            CATEGORY_MAP.put(normalise(cat), SHARD_A);
        }
        // Shard B
        for (String cat : new String[]{"Roupas", "Calcados", "Calçados", "Acessórios",
                "Acessorios"}) {
            CATEGORY_MAP.put(normalise(cat), SHARD_B);
        }
        // Shard C — Casa, Esporte, Outros (e fallback)
        for (String cat : new String[]{"Casa", "Esporte", "Outros"}) {
            CATEGORY_MAP.put(normalise(cat), SHARD_C);
        }
    }

    /**
     * Retorna o shard_id para uma dada categoria.
     * Categorias não mapeadas vão para shard_c (fallback "Outros").
     */
    public String route(String category) {
        if (category == null || category.isBlank()) {
            throw new IllegalArgumentException("category não pode ser vazia para roteamento de shard");
        }
        return CATEGORY_MAP.getOrDefault(normalise(category), SHARD_C);
    }

    private static String normalise(String s) {
        return s.trim().toLowerCase();
    }
}
