package com.scd.dbmanager;

import io.grpc.Server;
import io.grpc.ServerBuilder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

/**
 * Ponto de entrada do Gerente de BD.
 *
 * Carrega config.yaml (bootstrap automático), inicializa o
 * DBManagerService (shards, ReplicationManager, FailoverController)
 * e sobe o servidor gRPC na porta configurada.
 *
 * Uso:
 *   java -jar db-manager.jar
 */
public class DBManagerMain {

    private static final Logger log = LoggerFactory.getLogger(DBManagerMain.class);

    public static void main(String[] args) throws IOException, InterruptedException {
        ConfigLoader config = new ConfigLoader();

        final DBManagerService service = new DBManagerService(config);

        int port = config.getDbManagerPort();
        final Server server = ServerBuilder.forPort(port)
                .addService(service)
                .build()
                .start();

        log.info("Gerente de BD escutando na porta {}", port);

        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            log.info("Encerrando Gerente de BD...");
            server.shutdown();
            service.shutdown();
        }));

        server.awaitTermination();
    }
}
