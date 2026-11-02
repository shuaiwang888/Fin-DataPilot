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
          colorPrimary: "#087f73",
          colorInfo: "#4069b4",
          colorSuccess: "#12856f",
          colorWarning: "#a46316",
          colorError: "#be4b53",
          colorText: "#18283e",
          colorTextSecondary: "#63748a",
          colorBorder: "#dce3ec",
          colorBgLayout: "#f5f7fb",
          borderRadius: 10,
          fontFamily: '"IBM Plex Sans", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
          boxShadowSecondary: "0 18px 60px rgba(22, 43, 72, 0.12)",
        },
        components: {
          Button: {
            controlHeight: 36,
            fontWeight: 600,
          },
          Drawer: {
            colorBgElevated: "#ffffff",
          },
          Modal: { borderRadiusLG: 16 },
          Popover: { borderRadiusLG: 16 },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>
);
