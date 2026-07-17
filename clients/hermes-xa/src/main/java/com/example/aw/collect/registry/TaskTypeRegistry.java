package com.example.aw.collect.registry;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 前端 taskType → 库内 task_type、Hermes skill 名、发往模型的消息前缀。
 */
@Component
public class TaskTypeRegistry {

    public static final String DEFAULT_FRONTEND_TYPE = "collect";

    private static final Pattern SKILL_PREFIX = Pattern.compile(
            "^(account-intelligence-collect|account-expansion|account-intelligence-expand|"
                    + "account-intelligence-verification|account-intelligence-verify|"
                    + "account-intelligence-report|account-intelligence-profile)\\b",
            Pattern.CASE_INSENSITIVE);

    private final Map<String, TaskTypeDef> byFrontendType = new HashMap<String, TaskTypeDef>();

    public TaskTypeRegistry() {
        byFrontendType.put("collect", new TaskTypeDef(
                "collect", "account_collect", "account-intelligence-collect", "账号信息采集"));
        byFrontendType.put("expand", new TaskTypeDef(
                "expand", "account_expand", "account-expansion", "账号扩建"));
        byFrontendType.put("verify", new TaskTypeDef(
                "verify", "account_verify", "account-intelligence-verification", "账号核查"));
        byFrontendType.put("report", new TaskTypeDef(
                "report", "account_report", "account-intelligence-report", "画像写报"));
        byFrontendType.put("profile", new TaskTypeDef(
                "profile", "account_report", "account-intelligence-report", "画像写报"));
    }

    /**
     * 解析前端 taskType，未知值回退为 collect。
     */
    public TaskTypeDef resolve(String frontendType) {
        String key = normalize(frontendType);
        TaskTypeDef def = byFrontendType.get(key);
        if (def != null) {
            return def;
        }
        return byFrontendType.get(DEFAULT_FRONTEND_TYPE);
    }

    /**
     * 构造发往 Gateway 的 user message（确保带 skill 前缀供 Hermes 路由）。
     */
    public String buildGatewayMessage(String frontendType, String userMessage) {
        TaskTypeDef def = resolve(frontendType);
        String msg = userMessage == null ? "" : userMessage.trim();
        if (msg.isEmpty()) {
            return msg;
        }
        if (SKILL_PREFIX.matcher(msg).find()) {
            return msg;
        }
        if (msg.toLowerCase(Locale.ROOT).contains(def.skillName.toLowerCase(Locale.ROOT))) {
            return msg;
        }
        return def.skillName + " " + msg;
    }

    /**
     * 判断用户消息是否匹配某 task_type 的意图（Hook 兜底用，Java 侧与 Python 规则近似）。
     */
    public boolean matchesIntent(String dbTaskType, String userMessage) {
        String msg = userMessage == null ? "" : userMessage;
        if ("account_expand".equals(dbTaskType)) {
            return msg.matches("(?is).*(account-expansion|account-intelligence-expand|账号扩建|扩建).*");
        }
        if ("account_collect".equals(dbTaskType)) {
            return msg.matches("(?is).*(account-intelligence-collect|账号信息采集|采集).*");
        }
        if ("account_verify".equals(dbTaskType)) {
            return msg.matches("(?is).*(account-intelligence-verification|account-intelligence-verify|账号核查|核查).*");
        }
        if ("account_report".equals(dbTaskType)) {
            return msg.matches("(?is).*(account-intelligence-report|account-intelligence-profile|画像写报|写报).*");
        }
        return true;
    }

    /**
     * 解析前端短码或库内 task_type；无法识别返回 null（不做 collect 回退）。
     */
    public TaskTypeDef resolveExact(String type) {
        if (type == null || type.trim().isEmpty()) {
            return null;
        }
        String key = type.trim().toLowerCase(Locale.ROOT);
        TaskTypeDef byFrontend = byFrontendType.get(key);
        if (byFrontend != null) {
            return byFrontend;
        }
        if ("account_collect".equals(key)) {
            return byFrontendType.get("collect");
        }
        if ("account_expand".equals(key)) {
            return byFrontendType.get("expand");
        }
        if ("account_verify".equals(key)) {
            return byFrontendType.get("verify");
        }
        if ("account_report".equals(key)) {
            return byFrontendType.get("report");
        }
        return null;
    }

    /**
     * 库内 task_type → 中文标签；未知则原样返回。
     */
    public String labelOfDbTaskType(String dbTaskType) {
        TaskTypeDef def = resolveExact(dbTaskType);
        return def == null ? (dbTaskType == null ? "" : dbTaskType) : def.getLabel();
    }

    /**
     * 库内 task_type → 前端短码；未知返回空串。
     */
    public String frontendCodeOfDbTaskType(String dbTaskType) {
        TaskTypeDef def = resolveExact(dbTaskType);
        if (def == null) {
            return "";
        }
        // profile 与 report 同库类型，统一对外短码 report
        if ("profile".equals(def.getFrontendType())) {
            return "report";
        }
        return def.getFrontendType();
    }

    /**
     * 四业务对外枚举（不含 profile 别名）。
     */
    public List<TaskTypeDef> listPublicTypes() {
        List<TaskTypeDef> list = new ArrayList<TaskTypeDef>();
        list.add(byFrontendType.get("collect"));
        list.add(byFrontendType.get("expand"));
        list.add(byFrontendType.get("verify"));
        list.add(byFrontendType.get("report"));
        return list;
    }

    private String normalize(String frontendType) {
        if (frontendType == null || frontendType.trim().isEmpty()) {
            return DEFAULT_FRONTEND_TYPE;
        }
        return frontendType.trim().toLowerCase(Locale.ROOT);
    }

    public static final class TaskTypeDef {
        private final String frontendType;
        private final String dbTaskType;
        private final String skillName;
        private final String label;

        public TaskTypeDef(String frontendType, String dbTaskType, String skillName, String label) {
            this.frontendType = frontendType;
            this.dbTaskType = dbTaskType;
            this.skillName = skillName;
            this.label = label;
        }

        public String getFrontendType() {
            return frontendType;
        }

        public String getDbTaskType() {
            return dbTaskType;
        }

        public String getSkillName() {
            return skillName;
        }

        public String getLabel() {
            return label;
        }
    }
}
