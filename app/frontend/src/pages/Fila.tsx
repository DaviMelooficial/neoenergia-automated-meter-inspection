import { Alert, Badge, Button, Card, Grid, Group, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { mensagemDeErro } from "../api/client";
import { fila, MOTIVOS, operacao, type LeituraOut, type MotivoReprovacao } from "../api/servicos";
import { BotoesDecisao } from "../components/BotoesDecisao";
import { PainelLeitura } from "../components/PainelLeitura";
import { useAtalhos } from "../components/useAtalhos";
import { VisualizadorFoto } from "../components/VisualizadorFoto";

const CHAVE_FOTO = "foto";
const chaveFoto = (url: string) => [CHAVE_FOTO, url] as const;
const VIDA_FOTO_MS = 10 * 60 * 1000;

/**
 * Tela dos auditores. Meta: decisao em menos de 5 segundos por foto.
 * Reserva o item ao abrir (10 min), pre-carrega as proximas imagens enquanto o
 * auditor decide, e responde a A / R / Espaco / 1-6 / setas.
 */
export function Fila() {
  const qc = useQueryClient();
  const [atual, setAtual] = useState<LeituraOut | null>(null);
  const [filaVazia, setFilaVazia] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [motivo, setMotivo] = useState<MotivoReprovacao | null>(null);
  const [observacao, setObservacao] = useState("");
  const [decididas, setDecididas] = useState(0);
  const pulados = useRef<number[]>([]);
  const atualRef = useRef<LeituraOut | null>(null);
  atualRef.current = atual;

  const urlFoto = atual?.foto_principal?.url ?? null;
  const { data: srcFoto, isLoading: carregandoFoto } = useQuery({
    queryKey: chaveFoto(urlFoto ?? ""),
    queryFn: () => operacao.fotoBlob(urlFoto!),
    enabled: urlFoto !== null,
    staleTime: VIDA_FOTO_MS,
  });

  // Object URLs precisam ser revogados: o cache do React Query descarta a string,
  // mas o blob continuaria registrado no documento pelo turno inteiro do auditor.
  useEffect(() => {
    const cache = qc.getQueryCache();
    const revogar = (dados: unknown) => {
      if (typeof dados === "string" && dados.startsWith("blob:")) URL.revokeObjectURL(dados);
    };
    const parar = cache.subscribe((evento) => {
      if (evento.type === "removed" && evento.query.queryKey[0] === CHAVE_FOTO) {
        revogar(evento.query.state.data);
      }
    });
    return () => {
      parar();
      cache.findAll({ queryKey: [CHAVE_FOTO] }).forEach((q) => {
        revogar(q.state.data);
        qc.removeQueries({ queryKey: q.queryKey, exact: true });
      });
    };
  }, [qc]);

  // Ao sair da tela, devolve a reserva em vez de prende-la por 10 minutos.
  useEffect(() => {
    return () => {
      const id = atualRef.current?.id;
      if (id) fila.liberar(id).catch(() => undefined);
    };
  }, []);

  const carregarProxima = useCallback(async () => {
    setErro(null);
    setMotivo(null);
    setObservacao("");
    try {
      const proxima = await fila.proxima(pulados.current);
      setAtual(proxima);
      setFilaVazia(false);
    } catch (err) {
      setAtual(null);
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        setFilaVazia(true);
      } else {
        setErro(mensagemDeErro(err, "não foi possível carregar o próximo item"));
      }
    }
  }, []);

  // Um unico carregamento inicial, mesmo com o duplo mount do StrictMode em dev.
  const iniciou = useRef(false);
  useEffect(() => {
    if (iniciou.current) return;
    iniciou.current = true;
    void carregarProxima();
  }, [carregarProxima]);

  // Pre-carrega as fotos dos proximos candidatos enquanto o auditor decide.
  useEffect(() => {
    if (!atual) return;
    fila
      .listar({ por_pagina: 4 })
      .then((pagina) => {
        pagina.itens
          .filter((i) => i.id !== atual.id && !pulados.current.includes(i.id) && i.foto_principal)
          .slice(0, 2)
          .forEach((i) => {
            const url = i.foto_principal!.url;
            void qc.prefetchQuery({
              queryKey: chaveFoto(url),
              queryFn: () => operacao.fotoBlob(url),
              staleTime: VIDA_FOTO_MS,
            });
          });
      })
      .catch(() => undefined);
  }, [atual, qc]);

  const decidir = useCallback(
    async (decisao: "aprovada" | "reprovada") => {
      if (!atual || ocupado) return;
      if (decisao === "reprovada") {
        if (!motivo) {
          notifications.show({
            color: "orange",
            message: "Escolha o motivo da reprovação (teclas 1 a 6).",
          });
          return;
        }
        if (motivo === "outro" && observacao.trim() === "") {
          notifications.show({ color: "orange", message: "Descreva o motivo em 'observação'." });
          return;
        }
      }
      setOcupado(true);
      try {
        await fila.decidir(atual.id, {
          decisao,
          motivo: decisao === "reprovada" ? motivo : null,
          observacao: observacao.trim() || null,
        });
        setDecididas((n) => n + 1);
        await carregarProxima();
      } catch (err) {
        const status = axios.isAxiosError(err) ? err.response?.status : undefined;
        if (status === 409) {
          notifications.show({
            color: "yellow",
            message: "Este item já foi decidido ou está reservado por outro auditor.",
          });
          await carregarProxima();
        } else {
          setErro(mensagemDeErro(err, "não foi possível registrar a decisão"));
        }
      } finally {
        setOcupado(false);
      }
    },
    [atual, ocupado, motivo, observacao, carregarProxima],
  );

  const pular = useCallback(async () => {
    if (!atual || ocupado) return;
    setOcupado(true);
    try {
      await fila.liberar(atual.id);
      pulados.current = [...pulados.current, atual.id].slice(-200);
      await carregarProxima();
    } catch (err) {
      setErro(mensagemDeErro(err, "não foi possível pular o item"));
    } finally {
      setOcupado(false);
    }
  }, [atual, ocupado, carregarProxima]);

  const atalhos = useMemo(() => {
    const mapa: Record<string, () => void> = {
      a: () => void decidir("aprovada"),
      r: () => void decidir("reprovada"),
      " ": () => void pular(),
      escape: () => setMotivo(null),
    };
    for (const m of MOTIVOS) mapa[m.tecla] = () => setMotivo(m.valor);
    return mapa;
  }, [decidir, pular]);
  useAtalhos(atalhos, atual !== null);

  if (filaVazia) {
    return (
      <Alert color="green" title="Fila vazia" m="lg" role="status">
        Não há itens aguardando revisão.{" "}
        {decididas > 0 && `Você decidiu ${decididas} nesta sessão.`}
        <Group mt="sm">
          <Button variant="light" onClick={() => void carregarProxima()}>
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
          <Title order={3}>Fila de revisão</Title>
          {atual && (
            <Badge variant="light" ff="monospace" data-testid="leitura-atual">
              leitura #{atual.id}
            </Badge>
          )}
        </Group>
        <Text size="sm" c="dimmed" role="status" aria-live="polite">
          decididas nesta sessão: <b>{decididas}</b>
        </Text>
      </Group>
      {erro && (
        <Alert color="red" withCloseButton onClose={() => setErro(null)} role="alert">
          {erro}
        </Alert>
      )}
      <Grid gutter="md" align="stretch">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <VisualizadorFoto
            src={srcFoto ?? null}
            carregando={carregandoFoto || atual === null}
            alt={atual ? `foto da leitura ${atual.id}, medidor ${atual.medidores.join("/")}` : ""}
            ativo={atual !== null}
          />
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack gap="md">
            <Card withBorder>
              {atual ? <PainelLeitura leitura={atual} /> : <Text c="dimmed">carregando…</Text>}
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
