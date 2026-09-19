import { Alert, Center, Loader } from "@mantine/core";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import type { Papel } from "../api/servicos";
import { useAuth } from "./AuthContext";

/**
 * Exige sessao e papel minimo. Sem sessao redireciona para /login; com senha
 * inicial pendente forca /trocar-senha; sem papel suficiente mostra aviso.
 * O servidor valida tudo isso de novo: aqui e so a experiencia de uso.
 */
export function RotaProtegida({ minimo = "auditor" }: { minimo?: Papel }) {
  const { usuario, carregando, temPapel } = useAuth();
  const local = useLocation();

  if (carregando) {
    return (
      <Center h="60vh">
        <Loader />
      </Center>
    );
  }
  if (!usuario) {
    return <Navigate to="/login" replace state={{ de: local.pathname }} />;
  }
  if (usuario.precisa_trocar_senha && local.pathname !== "/trocar-senha") {
    return <Navigate to="/trocar-senha" replace />;
  }
  if (!temPapel(minimo)) {
    return (
      <Alert color="red" title="Sem permissão" m="lg" data-testid="sem-permissao">
        Esta área exige o papel <b>{minimo}</b> ou superior. Seu papel é <b>{usuario.papel}</b>.
      </Alert>
    );
  }
  return <Outlet />;
}
