import { lazy, Suspense, useEffect, useState } from "react";
import { App as AntdApp, Button, Tooltip } from "antd";
import { AppstoreOutlined, MenuOutlined } from "@ant-design/icons";
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

type ServiceState = "checking" | "online" | "offline";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [skillManagerMounted, setSkillManagerMounted] = useState(false);
  const [service, setService] = useState<ServiceState>("checking");
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
    api.health()
      .then((result) => {
        if (!controller.signal.aborted) setService(result.ok ? "online" : "offline");
      })
      .catch(() => {
        if (!controller.signal.aborted) setService("offline");
      });
    return () => controller.abort();
  }, [setSkills]);

  const serviceLabel = service === "online" ? "数据服务在线" : service === "offline" ? "数据服务未连接" : "正在连接数据服务";

  return (
    <AntdApp>
      <div className="fdp-app">
        <a className="fdp-skip" href="#research-desk">跳到研究台</a>
        <Sidebar mobileOpen={sidebarOpen} onMobileClose={() => setSidebarOpen(false)} />
        {sidebarOpen && (
          <button
            type="button"
            className="fdp-sidebar-scrim"
            aria-label="关闭侧边栏"
            onClick={() => setSidebarOpen(false)}
          />
        )}
        <div className="fdp-main" id="research-desk">
          <div className="fdp-toolbar">
            <div className="fdp-toolbar-heading">
              <Button
                className="fdp-mobile-menu"
                type="text"
                icon={<MenuOutlined />}
                aria-label="打开研究档案"
                onClick={() => setSidebarOpen(true)}
              />
              <div className="fdp-toolbar-copy">
                <span>{activeSession ? "当前备忘" : "新的研究"}</span>
                <strong>{activeSession?.title || "尚未开始"}</strong>
              </div>
            </div>
            <div className="fdp-toolbar-actions">
              <div className={`fdp-live-status is-${service}`} role="status" aria-label={serviceLabel}>
                <span className="fdp-live-dot" />
                {serviceLabel}
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
