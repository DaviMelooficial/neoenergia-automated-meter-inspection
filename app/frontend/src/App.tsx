import { Navigate, Route, Routes } from "react-router-dom";

import { RotaProtegida } from "./auth/RotaProtegida";
import { Auditoria } from "./pages/Auditoria";
import { Fila } from "./pages/Fila";
import { Layout } from "./pages/Layout";
import { Login } from "./pages/Login";
import { Metricas } from "./pages/Metricas";
import { Quarentena } from "./pages/Quarentena";
import { Rotulagem } from "./pages/Rotulagem";
import { TrocarSenha } from "./pages/TrocarSenha";
import { Usuarios } from "./pages/Usuarios";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />

      <Route element={<RotaProtegida />}>
        <Route path="/trocar-senha" element={<TrocarSenha />} />
        <Route element={<Layout />}>
          <Route path="/fila" element={<Fila />} />
          <Route path="/auditoria" element={<Auditoria />} />
          <Route element={<RotaProtegida minimo="supervisor" />}>
            <Route path="/quarentena" element={<Quarentena />} />
            <Route path="/metricas" element={<Metricas />} />
            <Route path="/rotulagem" element={<Rotulagem />} />
          </Route>
          <Route element={<RotaProtegida minimo="admin" />}>
            <Route path="/usuarios" element={<Usuarios />} />
          </Route>
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/fila" replace />} />
    </Routes>
  );
}
