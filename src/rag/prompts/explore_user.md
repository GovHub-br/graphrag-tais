# Panorama do catálogo (domínios do escopo)
{% for d in domains %}
- {{ d.domain }} (`{{ d.schema }}`): {{ d.table_count }} tabelas
{% endfor %}

# Tabelas recuperadas para este assunto (em ordem de relevância)
{% if documents %}{% for doc in documents %}
{{ doc.content }}
{% endfor %}{% else %}
(nenhuma tabela específica recuperada — responda com o panorama dos domínios
acima, explicando o que cada domínio cobre, e peça um recorte mais específico)
{% endif %}

# Relações de JOIN entre as tabelas recuperadas
{% if joins %}{% for j in joins %}
- {{ j.left_table }}.{{ j.left_col }} = {{ j.right_table }}.{{ j.right_col }} ({% if j.sql_join_eligible %}JOIN direto{% else %}requer normalização{% endif %})
{% endfor %}{% else %}
(nenhuma — as tabelas recuperadas não têm relação declarada entre si)
{% endif %}

# Pergunta do usuário
{{ query }}
