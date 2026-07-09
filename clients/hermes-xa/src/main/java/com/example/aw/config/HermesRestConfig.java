package com.example.aw.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

@Configuration
public class HermesRestConfig {

    @Bean("hermesRestTemplate")
    public RestTemplate hermesRestTemplate() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(60_000);        // 连接 60s
        factory.setReadTimeout(1_800_000);        // 读取 30 分钟 —— 画像任务必须
        return new RestTemplate(factory);
    }
}