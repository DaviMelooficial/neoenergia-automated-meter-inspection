import { Alert, Badge, Container, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";

type Componente = { ok: boolean; erro?: string | null; detalhe?: string; backend?: string };
type Health = { status: string; componentes: Record<string, Componente> };

/**
 * Tela provisoria do marco M0: confirma que o frontend fala com a API.
 * Substituida pelas rotas reais no marco M4.
 */
export function App() {
  const { data, isLoading, error } = useQuery<Health>({
    queryKey: ["health"],
    queryFn: async () => (await axios.get<Health>("/api/health")).data,
    refetchInterval: 5000,
  });

  return (
    <Container size="sm" py="xl">
      <Title order={2}>Inspeção de Fotos de Leitura</Title>
      <Text c="dimmed" size="sm" mb="lg">
        Neoenergia Pernambuco. Marco M0: ambiente e esqueleto.
      </Text>

      {isLoading && <Loader />}

      {error && (
        <Alert color="red" title="API inacessível">
          Não foi possível consultar /api/health. Verifique se o backend está no ar.
        </Alert>
      )}

      {data && (
        <Stack gap="xs">
          <Group>
            <Text fw={600}>Status geral</Text>
            <Badge color={data.status === "ok" ? "green" : "yellow"}>{data.status}</Badge>
          </Group>
          {Object.entries(data.componentes).map(([nome, c]) => (
            <Group key={nome} justify="space-between">
              <Text>{nome}</Text>
              <Group gap="xs">
                {c.detalhe && (
                  <Text size="xs" c="dimmed">
                    {c.detalhe}
                  </Text>
                )}
                <Badge color={c.ok ? "green" : "red"}>{c.ok ? "ok" : "falha"}</Badge>
              </Group>
            </Group>
          ))}
        </Stack>
      )}
    </Container>
  );
}
