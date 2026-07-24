package com.example.aw.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 视频本地文件静态映射：/video-files/** → hermes.video.local-dir
 */
@Configuration
public class VideoStaticResourceConfig implements WebMvcConfigurer {

    @Value("${hermes.video.local-dir:D:/hermes-xa/data/video_bytes}")
    private String videoLocalDir;

    @Value("${hermes.video.url-prefix:/video-files}")
    private String videoUrlPrefix;

    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        String dir = videoLocalDir == null ? "" : videoLocalDir.trim().replace("\\", "/");
        if (dir.isEmpty()) {
            return;
        }
        if (!dir.endsWith("/")) {
            dir = dir + "/";
        }
        String prefix = videoUrlPrefix == null ? "/video-files" : videoUrlPrefix.trim();
        if (!prefix.startsWith("/")) {
            prefix = "/" + prefix;
        }
        if (prefix.endsWith("/")) {
            prefix = prefix.substring(0, prefix.length() - 1);
        }
        registry.addResourceHandler(prefix + "/**")
                .addResourceLocations("file:" + dir);
    }
}
