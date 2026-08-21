# Schema disponível (subgrafo recuperado)
{% for doc in documents %}
{{ doc.content }}
{% endfor %}

# Relações de JOIN disponíveis
{% if joins %}{% for j in joins %}
- {{ j.left_table }}.{{ j.left_col }} = {{ j.right_table }}.{{ j.right_col }} ({% if j.sql_join_eligible %}JOIN direto{% else %}requer normalização{% endif %}, regra={{ j.join_rule }}, cardinalidade={{ j.cardinality }})
{% endfor %}{% else %}
(nenhuma relação de JOIN entre as tabelas recuperadas)
{% endif %}

# Pergunta do usuário
{{ query }}
