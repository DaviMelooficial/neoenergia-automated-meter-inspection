import { Alert, Badge, Button, Card, Grid, Group, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { mensagemDeErro } from "../api/client";
import {
  auditoria,
  MOTIVOS,
  operacao,
  type ItemAuditoria,
  type LeituraOut,
  type MotivoReprovacao,
} from "../api/servicos";
import { BotoesDecisao } from "../components/BotoesDecisao";
import { PainelLeitura } from "../components/PainelLeitura";
import { useAtalhos } from "../components/useAtalhos";
import { VisualizadorFoto } from "../components/VisualizadorFoto";

/** O painel da fila espera uma leitura; o item de auditoria nao traz status,
 * historico nem revisoes (cegueira do servidor), entao esses campos vao vazios. */
function comoLeitura(item: ItemAuditoria): LeituraOut {
  return {
    id: item.auditoria_id,
    lote: item.lote,
    linha_csv: item.linha_csv,
    medidores: item.medidores,
    posicao_lida: item.posicao_lida,
    nota_codigo: item.nota_codigo,
    status_atual: "em_auditoria",
    foto_principal: item.foto_principal,
    reservado_por: null,
    reservado_ate: item.reservado_ate,
    criado_em: new Date().toISOString(),
    nota: item.nota,
    fotos: item.fotos,
    historico: [],
    inferencias: item.inferencias,
    quarentena: item.quarentena,
    revisoes: [],
  };
}

/**
 * Auditoria amostral (M8): o segundo revisor decide sem ver a primeira decisao.
 * O servidor nunca entrega a quem decidiu o item e recusa (403) se ele tentar.
 */
export function Auditoria() {
  const [atual, setAtual] = useState<ItemAuditoria | null>(null);
  const [vazia, setVazia] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [motivo, setMotivo] = useState<MotivoReprovacao | null>(null);
  const [observacao, setObservacao] = useState("");
  const [feitas, setFeitas] = useState(0);
  const [pausada, setPausada] = useState(false);
  const atualRef = useRef<ItemAuditoria | null>(null);
  atualRef.current = atual;

  const url = atual?.foto_principal?.url ?? null;
  const { data: src, isLoading } = useQuery({
    queryKey: ["foto", url ?? ""],
    queryFn: () => operacao.fotoBlob(url!),
    enabled: url !== null,
  });

  useEffect(() => {
    return () => {
      const id = atualRef.current?.auditoria_id;
      if (id) auditoria.liberar(id).catch(() => undefined);
    };
  }, []);

  const carregar = useCallback(async () => {
    setErro(null);
    setMotivo(null);
    setObservacao("");
    try {
      setAtual(await auditoria.proxima());
      setVazia(false);
      setPausada(false);
    } catch (err) {
      setAtual(null);
      if (axios.isAxiosError(err) && err.response?.status === 404) setVazia(true);
      else setErro(mensagemDeErro(err, "não foi possível carregar a próxima auditoria"));
    }
  }, []);

  const iniciou = useRef(false);
  useEffect(() => {
    if (iniciou.current) return;
    iniciou.current = true;
    void carregar();
  }, [carregar]);

  const decidir = useCallback(
    async (decisao: "aprovada" | "reprovada") => {
      if (!atual || ocupado) return;
      if (decisao === "reprovada" && !motivo) {
        notifications.show({ color: "orange", message: "Escolha o motivo da reprovação (teclas 1 a 6)." });
        return;
      }
      if (decisao === "reprovada" && motivo === "outro" && observacao.trim() === "") {
        notifications.show({ color: "orange", message: "Descreva o motivo em 'observação'." });
        return;
      }
      setOcupado(true);
      try {
        await auditoria.decidir(atual.auditoria_id, {
          decisao,
          motivo: decisao === "reprovada" ? motivo : null,
          observacao: observacao.trim() || null,
        });
        setFeitas((n) => n + 1);
        await carregar();
      } catch (err) {
        const status = axios.isAxiosError(err) ? err.response?.status : undefined;
        if (status === 409 || status === 403) {
          notifications.show({ color: "yellow", message: mensagemDeErro(err, "auditoria indisponível") });
          await carregar();
        } else {
          setErro(mensagemDeErro(err, "não foi possível registrar a auditoria"));
        }
      } finally {
        setOcupado(false);
      }
    },
    [atual, ocupado, motivo, observacao, carregar],
  );

  const pular = useCallback(async () => {
    if (!atual || ocupado) return;
    setOcupado(true);
    try {
      await auditoria.liberar(atual.auditoria_id);
      setAtual(null);
      setPausada(true);
    } finally {
      setOcupado(false);
    }
  }, [atual, ocupado]);

  const atalhos = useMemo(() => {
    const mapa: Record<string, () => void> = {
      a: () => void decidir("aprovada"),
      r: () => void decidir("reprovada"),
      escape: () => setMotivo(null),
    };
    for (const m of MOTIVOS) mapa[m.tecla] = () => setMotivo(m.valor);
    return mapa;
  }, [decidir]);
  useAtalhos(atalhos, atual !== null);

  if (pausada) {
    return (
      <Alert color="blue" title="Auditoria devolvida" m="lg" role="status">
        O item voltou para os outros revisores.
        <Group mt="sm">
          <Button variant="light" onClick={() => void carregar()}>
            Próxima auditoria
          </Button>
        </Group>
      </Alert>
    );
  }

  if (vazia) {
    return (
      <Alert color="green" title="Nenhuma auditoria pendente para você" m="lg" role="status">
        Os itens sorteados que você mesmo decidiu nunca aparecem aqui.{" "}
        {feitas > 0 && `Você auditou ${feitas} nesta sessão.`}
        <Group mt="sm">
          <Button variant="light" onClick={() => void carregar()}>
            Verificar de novo
          </Button>
        </Group>
      </Alert>
    );
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Group gap="xs">
          <Title order={3}>Auditoria amostral</Title>
          {atual && (
            <Badge variant="light" ff="monospace" data-testid="auditoria-atual">
              auditoria #{atual.auditoria_id}
            </Badge>
          )}
        </Group>
        <Text size="sm" c="dimmed" role="status" aria-live="polite">
          restantes: <b>{atual?.restantes ?? 0}</b> · feitas nesta sessão: <b>{feitas}</b>
        </Text>
      </Group>
      <Text size="sm" c="dimmed">
        Item já decidido por outro auditor e sorteado para conferência. A decisão anterior não é mostrada:
        julgue a foto como se fosse a primeira vez.
      </Text>
      {erro && (
        <Alert color="red" withCloseButton onClose={() => setErro(null)} role="alert">
          {erro}
        </Alert>
      )}
      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <VisualizadorFoto
            src={src ?? null}
            carregando={isLoading || atual === null}
            alt={atual ? `foto em auditoria, medidor ${atual.medidores.join("/")}` : ""}
            ativo={atual !== null}
          />
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack gap="md">
            <Card withBorder>
              {atual ? <PainelLeitura leitura={comoLeitura(atual)} /> : <Text c="dimmed">carregando…</Text>}
            </Card>
            <Card withBorder>
              <BotoesDecisao
                motivo={motivo}
                observacao={observacao}
                ocupado={ocupado || atual === null}
                onMotivo={setMotivo}
                onObservacao={setObservacao}
                onAprovar={() => void decidir("aprovada")}
                onReprovar={() => void decidir("reprovada")}
                onPular={() => void pular()}
              />
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
