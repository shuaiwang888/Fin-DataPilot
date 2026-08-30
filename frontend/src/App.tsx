import { lazy, Suspense, useEffect, useState } from "react";
import { App as AntdApp, Button, Tooltip } from "antd";
import {
  AppstoreOutlined,
  BarChartOutlined,
  MenuOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Sidebar } from "./components/layout/Sidebar";
import { ChatWindow } from "./components/chat/ChatWindow";
import { MessageInput } from "./components/chat/MessageInput";
import { useSessionStore } from "./stores/sessionStore";
import { useSkillStore } from "./stores/skillStore";
import { api } from "./lib/api";

const SkillManagerDrawer = lazy(() =>
  import("./components/layout/SkillManagerDrawer").then((module) => ({
    default: module.SkillManagerDrawer,
  })),
);

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [skillManagerMounted, setSkillManagerMounted] = useState(false);
  const sessions = useSessionStore((s) => s.sessions);
  const activeId = useSessionStore((s) => s.activeId);
  const openSkillDrawer = useSkillStore((s) => s.setDrawerOpen);
  const skillDrawerOpen = useSkillStore((s) => s.drawerOpen);
  const setSkills = useSkillStore((s) => s.setSkills);
  const enabledSkills = useSkillStore((s) => s.skills.filter((skill) => skill.enabled).length);
  const activeSession = sessions.find((session) => session.id === activeId);

  useEffect(() => {
    if (skillDrawerOpen) setSkillManagerMounted(true);
  }, [skillDrawerOpen]);

  // Keep the small capability summary available in the toolbar while the
  // feature-heavy manager UI stays outside the initial JavaScript bundle.
  useEffect(() => {
    const controller = new AbortController();
    api.listSkills(controller.signal)
      .then((result) => setSkills(result.skills))
      .catch((error: Error) => {
        if (error.name !== "AbortError") {
          // Offline startup is non-fatal; the drawer can retry on refresh.
        }
      });
    return () => controller.abort();
  }, [setSkills]);

  return (
    <AntdApp>
      <div className="fdp-app">
        <Sidebar mobileOpen={sidebarOpen} onMobileClose={() => setSidebarOpen(false)} />
        {sidebarOpen && (
          <button
            type="button"
            className="fdp-sidebar-scrim"
            aria-label="关闭侧边栏"
            onClick={() => setSidebarOpen(false)}
          />
        )}
        <div className="fdp-main">
          <div className="fdp-toolbar">
            <div className="fdp-toolbar-heading">
              <Button
                className="fdp-mobile-menu"
                type="text"
                icon={<MenuOutlined />}
                aria-label="打开侧边栏"
                onClick={() => setSidebarOpen(true)}
              />
              <div className="fdp-toolbar-icon" aria-hidden="true"><BarChartOutlined /></div>
              <div className="fdp-toolbar-copy">
                <strong>{activeSession?.title || "新建分析"}</strong>
                <span>{activeSession ? "证据驱动的金融研究" : "从一个问题开始"}</span>
              </div>
            </div>
            <div className="fdp-toolbar-actions">
              <div className="fdp-live-status" role="status" aria-label="数据服务正常">
                <span className="fdp-live-dot" />
                服务在线
              </div>
              <Tooltip title="查看可用分析能力">
                <Button
                  className="fdp-capability-button"
                  icon={<AppstoreOutlined />}
                  onClick={() => openSkillDrawer(true)}
                >
                  能力 <span className="fdp-capability-count">{enabledSkills || 0}</span>
                </Button>
              </Tooltip>
              <Tooltip title="当前为匿名安全会话">
                <Button
                  type="text"
                  className="fdp-toolbar-quiet"
                  icon={<SafetyCertificateOutlined />}
                  aria-label="匿名安全会话"
                />
              </Tooltip>
            </div>
          </div>
          <ChatWindow />
          <MessageInput />
        </div>
        {skillManagerMounted && (
          <Suspense fallback={null}>
            <SkillManagerDrawer />
          </Suspense>
        )}
      </div>
    </AntdApp>
  );
}
