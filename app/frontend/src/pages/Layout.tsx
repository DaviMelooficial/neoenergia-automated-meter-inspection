import {
  ActionIcon,
  AppShell,
  Badge,
  Button,
  Container,
  Group,
  Text,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const estiloLink = ({ isActive }: { isActive: boolean }) => ({
  padding: "6px 10px",
  borderRadius: 6,
  textDecoration: "none",
  fontSize: 14,
  fontWeight: isActive ? 700 : 500,
  color: isActive ? "var(--mantine-color-text)" : "var(--mantine-color-dimmed)",
  background: isActive ? "var(--mantine-color-green-light)" : "transparent",
});

export function Layout() {
  const { usuario, logout, temPapel } = useAuth();
  const navegar = useNavigate();
  const { setColorScheme } = useMantineColorScheme();
  const esquema = useComputedColorScheme("light");

  return (
    <AppShell header={{ height: 52 }} padding="md">
      <AppShell.Header>
        <Container fluid h="100%">
          <Group h="100%" justify="space-between">
            <Group gap="xs">
              <Text fw={700}>
                Inspeção de Fotos
              </Text>
              <NavLink to="/fila" style={estiloLink}>
                Fila
              </NavLink>
              {/* so o papel auditor audita (M8); supervisor e admin leem as metricas */}
              {usuario?.papel === "auditor" && (
                <NavLink to="/auditoria" style={estiloLink}>
                  Auditoria
                </NavLink>
              )}
              {temPapel("supervisor") && (
                <>
                  <NavLink to="/quarentena" style={estiloLink}>
                    Quarentena
                  </NavLink>
                  <NavLink to="/rotulagem" style={estiloLink}>
                    Rotulagem
                  </NavLink>
                  <NavLink to="/metricas" style={estiloLink}>
                    Métricas
                  </NavLink>
                </>
              )}
              {temPapel("admin") && (
                <NavLink to="/usuarios" style={estiloLink}>
                  Usuários
                </NavLink>
              )}
            </Group>
            <Group gap="xs">
              <ActionIcon
                variant="default"
                aria-label={esquema === "dark" ? "usar tema claro" : "usar tema escuro"}
                title={esquema === "dark" ? "tema claro" : "tema escuro"}
                onClick={() => setColorScheme(esquema === "dark" ? "light" : "dark")}
              >
                {esquema === "dark" ? "☀" : "☾"}
              </ActionIcon>
              {usuario && (
                <>
                  <Text size="sm">{usuario.nome}</Text>
                  <Badge variant="light">{usuario.papel}</Badge>
                  <Button size="xs" variant="subtle" onClick={() => navegar("/trocar-senha")}>
                    senha
                  </Button>
                  <Button size="xs" variant="default" onClick={() => void logout().then(() => navegar("/login"))}>
                    Sair
                  </Button>
                </>
              )}
            </Group>
          </Group>
        </Container>
      </AppShell.Header>
      <AppShell.Main>
        <Container fluid>
          <Outlet />
        </Container>
      </AppShell.Main>
    </AppShell>
  );
}
