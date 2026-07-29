package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;
import org.springframework.util.StreamUtils;

import javax.annotation.PostConstruct;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

/**
 * 04 写报七大父阶段旁白文案（classpath: report-phase-narratives.json）。
 * 仅配置加载；挂到 tree 节点由 {@link TaskTreeQueryService} 完成。
 */
@Component
public class ReportPhaseNarrativeConfig {

    private static final Logger log = LoggerFactory.getLogger(ReportPhaseNarrativeConfig.class);
    private static final String RESOURCE = "report-phase-narratives.json";

    /** stepKey → {running_content, completed_content} */
    private Map<String, Map<String, String>> byPhase = Collections.emptyMap();

    @PostConstruct
    public void load() {
        try {
            ClassPathResource res = new ClassPathResource(RESOURCE);
            if (!res.exists()) {
                log.warn("旁白配置不存在: classpath:{}", RESOURCE);
                return;
            }
            InputStream in = res.getInputStream();
            try {
                String text = StreamUtils.copyToString(in, StandardCharsets.UTF_8);
                JSONObject root = JSON.parseObject(text);
                JSONObject phases = root == null ? null : root.getJSONObject("phases");
                if (phases == null || phases.isEmpty()) {
                    log.warn("旁白配置 phases 为空: {}", RESOURCE);
                    return;
                }
                Map<String, Map<String, String>> map = new HashMap<String, Map<String, String>>();
                for (String key : phases.keySet()) {
                    JSONObject one = phases.getJSONObject(key);
                    if (one == null) {
                        continue;
                    }
                    Map<String, String> pair = new HashMap<String, String>();
                    pair.put("running_content", str(one.getString("running_content")));
                    pair.put("completed_content", str(one.getString("completed_content")));
                    map.put(key, pair);
                }
                byPhase = Collections.unmodifiableMap(map);
                log.info("已加载写报阶段旁白 {} 条", byPhase.size());
            } finally {
                in.close();
            }
        } catch (Exception e) {
            log.warn("加载旁白配置失败: {}", e.toString());
            byPhase = Collections.emptyMap();
        }
    }

    /**
     * 取某父壳旁白；无配置返回 null（调用方不写字段）。
     */
    public Map<String, String> get(String phaseStepKey) {
        if (phaseStepKey == null || phaseStepKey.isEmpty()) {
            return null;
        }
        return byPhase.get(phaseStepKey);
    }

    private static String str(String s) {
        return s == null ? "" : s;
    }
}
