import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import "antd/dist/reset.css";
import App from "./App";
import "./styles/global.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#1c4534",
          colorInfo: "#3d5270",
          colorSuccess: "#1f6b45",
          colorWarning: "#8d6230",
          colorError: "#9b2c2c",
          colorText: "#1a1814",
          colorTextSecondary: "#6f675c",
          colorBorder: "rgba(36, 28, 18, 0.16)",
          colorBgLayout: "#f4efe6",
          borderRadius: 2,
          fontFamily: '"IBM Plex Sans", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
          boxShadowSecondary: "0 18px 40px rgba(28, 22, 12, 0.12)",
        },
        components: {
          Button: {
            controlHeight: 36,
            fontWeight: 600,
          },
          Drawer: {
            colorBgElevated: "#f7f3eb",
          },
          Modal: { borderRadiusLG: 2 },
          Popover: { borderRadiusLG: 2 },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>
);
