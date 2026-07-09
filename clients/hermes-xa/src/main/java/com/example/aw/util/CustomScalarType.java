package com.example.aw.util;

import graphql.schema.Coercing;
import graphql.schema.GraphQLScalarType;
import org.springframework.stereotype.Component;

@Component
class CustomScalarType extends GraphQLScalarType {

    public CustomScalarType() {
        super("Object", "Object type", new Coercing<Object, Object>() {
            @Override
            public Object serialize(Object o) {
                return o;
            }

            @Override
            public Object parseValue(Object o) {
                return o;
            }

            @Override
            public Object parseLiteral(Object o) {
                return o;
            }
        });
    }
}
