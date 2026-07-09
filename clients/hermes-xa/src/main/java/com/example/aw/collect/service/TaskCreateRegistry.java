package com.example.aw.collect.service;

import com.example.aw.collect.registry.TaskTypeRegistry;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 按 taskType 分发预建任务服务。
 */
@Service
public class TaskCreateRegistry {

    private final Map<String, TaskCreateService> byFrontendType = new HashMap<String, TaskCreateService>();

    @Autowired
    public TaskCreateRegistry(List<TaskCreateService> services, TaskTypeRegistry taskTypeRegistry) {
        Map<String, TaskCreateService> byDb = new HashMap<String, TaskCreateService>();
        for (TaskCreateService service : services) {
            byDb.put(service.dbTaskType(), service);
        }
        for (String frontend : new String[]{"collect", "expand", "verify", "profile"}) {
            TaskTypeRegistry.TaskTypeDef def = taskTypeRegistry.resolve(frontend);
            TaskCreateService svc = byDb.get(def.getDbTaskType());
            if (svc != null) {
                byFrontendType.put(def.getFrontendType(), svc);
            }
        }
    }

    public TaskCreateService resolve(String frontendType) {
        String key = frontendType == null || frontendType.trim().isEmpty()
                ? TaskTypeRegistry.DEFAULT_FRONTEND_TYPE
                : frontendType.trim().toLowerCase();
        TaskCreateService service = byFrontendType.get(key);
        if (service == null) {
            throw new IllegalArgumentException("taskType 尚未实现: " + key);
        }
        return service;
    }
}
