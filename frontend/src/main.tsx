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
          colorPrimary: "#0f7b68",
          colorInfo: "#2877c7",
          colorSuccess: "#168463",
          colorText: "#1d2125",
          colorTextSecondary: "#737d84",
          colorBorder: "rgba(29, 42, 47, 0.12)",
          colorBgLayout: "#f4f5f6",
          borderRadius: 12,
          fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Helvetica Neue", "Microsoft YaHei", sans-serif',
          boxShadowSecondary: "0 24px 70px rgba(18, 30, 34, 0.16)",
        },
        components: {
          Button: {
            controlHeight: 36,
            fontWeight: 600,
          },
          Drawer: {
            colorBgElevated: "rgba(248, 249, 250, 0.96)",
          },
          Modal: { borderRadiusLG: 20 },
          Popover: { borderRadiusLG: 14 },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>
);
