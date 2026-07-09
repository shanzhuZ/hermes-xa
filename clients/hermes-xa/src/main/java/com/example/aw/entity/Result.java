package com.example.aw.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class Result {
    private Integer code;
    private String message;
    private Integer totalCount;
    private Integer totalPage;
    private Object records;
}
