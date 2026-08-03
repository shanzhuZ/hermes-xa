package com.example.aw.config;

import org.apache.http.HttpHost;
import org.apache.http.auth.AuthScope;
import org.apache.http.auth.UsernamePasswordCredentials;
import org.apache.http.client.CredentialsProvider;
import org.apache.http.conn.ssl.NoopHostnameVerifier;
import org.apache.http.impl.client.BasicCredentialsProvider;
import org.apache.http.ssl.SSLContextBuilder;
import org.elasticsearch.client.RestClient;
import org.elasticsearch.client.RestClientBuilder;
import org.elasticsearch.client.RestHighLevelClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import javax.net.ssl.SSLContext;

/**
 * 大屏 ES 客户端（与 aw 项目一致：HTTPS + Basic Auth + 信任证书）。
 * 连接目标与 scripts/agent_node/seed_es.py 的 CONFIG 对齐。
 */
@Configuration
public class ElasticsearchConfig {

    private static final Logger logger = LoggerFactory.getLogger(ElasticsearchConfig.class);

    @Value("${es.connect-timeout:10000}")
    private int connectTimeout;

    /** 深分页单次查询可能较慢，默认10分钟 */
    @Value("${es.socket-timeout:600000}")
    private int socketTimeout;

    @Value("${es.host:192.168.3.226}")
    private String host;

    @Value("${es.port:9201}")
    private int port;

    @Value("${es.scheme:https}")
    private String scheme;

    @Value("${es.username:elastic}")
    private String username;

    @Value("${es.password:i7Smzj2wVUndynjJXXv76A==}")
    private String password;

    @Bean(name = "restHighLevelClient5602")
    public RestHighLevelClient initEs5602() {
        logger.info("初始化 ES 客户端 host={}:{} scheme={}", host, Integer.valueOf(port), scheme);
        long start = System.currentTimeMillis();

        try {
            HttpHost httpHost = new HttpHost(host, port, scheme);

            // 忽略 SSL 证书校验（内网自签）
            SSLContext sslContext = SSLContextBuilder.create()
                    .loadTrustMaterial((chain, authType) -> true)
                    .build();

            final CredentialsProvider credentialsProvider = new BasicCredentialsProvider();
            credentialsProvider.setCredentials(
                    AuthScope.ANY,
                    new UsernamePasswordCredentials(username, password)
            );

            RestClientBuilder builder = RestClient.builder(httpHost)
                    .setHttpClientConfigCallback(httpClientBuilder ->
                            httpClientBuilder
                                    .setSSLContext(sslContext)
                                    .setSSLHostnameVerifier(NoopHostnameVerifier.INSTANCE)
                                    .setDefaultCredentialsProvider(credentialsProvider)
                                    .setMaxConnTotal(50)
                                    .setMaxConnPerRoute(50)
                    )
                    .setRequestConfigCallback(requestConfigBuilder ->
                            requestConfigBuilder
                                    .setConnectTimeout(connectTimeout)
                                    .setSocketTimeout(socketTimeout)
                                    .setConnectionRequestTimeout(connectTimeout)
                    );

            RestHighLevelClient client = new RestHighLevelClient(builder);
            logger.info("ES客户端初始化完毕，耗时 {} ms", Long.valueOf(System.currentTimeMillis() - start));
            return client;
        } catch (Exception e) {
            logger.error("初始化 ES 客户端失败", e);
            throw new RuntimeException(e);
        }
    }
}
