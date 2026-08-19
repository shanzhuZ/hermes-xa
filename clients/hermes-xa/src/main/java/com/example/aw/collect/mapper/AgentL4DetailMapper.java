package com.example.aw.collect.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.Map;

/**
 * 大屏 L4 节点详情 hermes_agent_l4_detail。
 */
@Mapper
public interface AgentL4DetailMapper {

    /**
     * 按 ES 节点 id（对应表字段 node_id）查询整行。
     */
    Map<String, Object> selectByNodeId(@Param("nodeId") String nodeId);
}
