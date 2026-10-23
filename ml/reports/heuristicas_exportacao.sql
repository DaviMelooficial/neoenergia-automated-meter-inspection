-- Exportacao usada por mlinsp.relatorio_heuristicas (make relatorio-heuristicas).
-- Uma linha por leitura com foto principal e resultado de heuristica@v1 (o mais recente).
COPY (
    SELECT f.nome_arquivo AS arquivo,
           lo.nome        AS lote,
           le.status_atual AS status,
           r.saida::text  AS saida,
           r.latencia_ms,
           rh.decisao     AS decisao_humana,
           rh.email       AS email_revisor
      FROM leituras le
      JOIN lotes lo ON lo.id = le.lote_id
      JOIN fotos f  ON f.leitura_id = le.id AND f.principal
      JOIN LATERAL (
            SELECT saida, latencia_ms FROM resultados_inferencia ri
             WHERE ri.foto_id = f.id AND ri.modelo = 'heuristica@v1'
             ORDER BY ri.id DESC LIMIT 1) r ON true
      LEFT JOIN LATERAL (
            SELECT rv.decisao, u.email FROM revisoes_humanas rv
              JOIN usuarios u ON u.id = rv.usuario_id
             WHERE rv.leitura_id = le.id
             ORDER BY rv.id DESC LIMIT 1) rh ON true
     ORDER BY f.nome_arquivo
) TO STDOUT WITH CSV HEADER;
