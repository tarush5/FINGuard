{#
    Division that yields NULL instead of raising when the denominator is zero.
    Used throughout the marts so a quiet day never breaks a dashboard.
#}
{% macro safe_divide(numerator, denominator) %}
    case
        when ({{ denominator }}) is null or ({{ denominator }}) = 0 then null
        else ({{ numerator }})::numeric / ({{ denominator }})
    end
{% endmacro %}
