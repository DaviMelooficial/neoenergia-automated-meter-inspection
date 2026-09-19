import { AppShell, Badge, Button, Container, Group, Text } from "@mantine/core";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const estiloLink = ({ isActive }: { isActive: boolean }) => ({
  padding: "6px 10px",
  borderRadius: 6,
  textDecoration: "none",
  fontSize: 14,
  fontWeight: isActive ? 700 : 500,
  color: isActive ? "#0F2A44" : "#5B6773",
  background: isActive ? "#E4F4EB" : "transparent",
});

export function Layout() {
  const { usuario, logout, temPapel } = useAuth();
  const navegar = useNavigate();

  return (
    <AppShell header={{ height: 52 }} padding="md">
      <AppShell.Header>
        <Container fluid h="100%">
          <Group h="100%" justify="space-between">
            <Group gap="xs">
              <Text fw={700} c="#0F2A44">
                Inspeção de Fotos
              </Text>
              <NavLink to="/fila" style={estiloLink}>
                Fila
              </NavLink>
              {temPapel("supervisor") && (
                <>
                  <NavLink to="/quarentena" style={estiloLink}>
                    Quarentena
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
