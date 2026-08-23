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
          colorPrimary: "#176b5b",
          colorInfo: "#176b5b",
          colorSuccess: "#168564",
          colorText: "#17231f",
          colorTextSecondary: "#65736e",
          colorBorder: "#dfe7e3",
          colorBgLayout: "#f3f6f4",
          borderRadius: 12,
          fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
          boxShadowSecondary: "0 18px 48px rgba(25, 46, 39, 0.14)",
        },
        components: {
          Button: {
            controlHeight: 38,
            fontWeight: 600,
          },
          Drawer: {
            colorBgElevated: "#f8faf9",
          },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>
);
