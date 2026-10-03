import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { definirToken, encerrarSessao, registrarPerdaDeSessao, renovarToken } from "../api/client";
import { auth, ORDEM_PAPEL, type Papel, type UsuarioOut } from "../api/servicos";

export interface EstadoAuth {
  usuario: UsuarioOut | null;
  carregando: boolean;
  login: (email: string, senha: string) => Promise<UsuarioOut>;
  logout: () => Promise<void>;
  recarregar: () => Promise<void>;
  temPapel: (minimo: Papel) => boolean;
}

export const AuthContext = createContext<EstadoAuth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [usuario, setUsuario] = useState<UsuarioOut | null>(null);
  const [carregando, setCarregando] = useState(true);

  const recarregar = useCallback(async () => {
    setUsuario(await auth.me());
  }, []);

  // Ao abrir a aplicacao tenta restaurar a sessao pelo cookie de refresh.
  useEffect(() => {
    registrarPerdaDeSessao(() => setUsuario(null));
    renovarToken()
      .then(() => auth.me())
      .then(setUsuario)
      .catch(() => setUsuario(null))
      .finally(() => setCarregando(false));
    return () => registrarPerdaDeSessao(null);
  }, []);

  const login = useCallback(async (email: string, senha: string) => {
    const token = await auth.login(email, senha);
    definirToken(token.access_token);
    const me = await auth.me();
    setUsuario(me);
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await auth.logout();
    } finally {
      encerrarSessao();
      setUsuario(null);
    }
  }, []);

  const valor = useMemo<EstadoAuth>(
    () => ({
      usuario,
      carregando,
      login,
      logout,
      recarregar,
      temPapel: (minimo) => usuario !== null && ORDEM_PAPEL[usuario.papel] >= ORDEM_PAPEL[minimo],
    }),
    [usuario, carregando, login, logout, recarregar],
  );

  return <AuthContext.Provider value={valor}>{children}</AuthContext.Provider>;
}

export function useAuth(): EstadoAuth {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth precisa estar dentro de AuthProvider");
  return ctx;
}
