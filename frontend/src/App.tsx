import { useState } from "react";
import { App as AntdApp, Button, Tooltip } from "antd";
import {
  AppstoreOutlined,
  BarChartOutlined,
  MenuOutlined,
  MoreOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Sidebar } from "./components/layout/Sidebar";
import { SkillManagerDrawer } from "./components/layout/SkillManagerDrawer";
import { ChatWindow } from "./components/chat/ChatWindow";
import { MessageInput } from "./components/chat/MessageInput";
import { useSessionStore } from "./stores/sessionStore";
import { useSkillStore } from "./stores/skillStore";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const sessions = useSessionStore((s) => s.sessions);
  const activeId = useSessionStore((s) => s.activeId);
  const openSkillDrawer = useSkillStore((s) => s.setDrawerOpen);
  const enabledSkills = useSkillStore((s) => s.skills.filter((skill) => skill.enabled).length);
  const activeSession = sessions.find((session) => session.id === activeId);

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
              <div className="fdp-toolbar-icon"><BarChartOutlined /></div>
              <div className="fdp-toolbar-copy">
                <strong>{activeSession?.title || "新建分析"}</strong>
                <span>{activeSession ? "智能金融数据会话" : "从一个问题开始你的研究"}</span>
              </div>
            </div>
            <div className="fdp-toolbar-actions">
              <div className="fdp-live-status">
                <span className="fdp-live-dot" />
                数据服务正常
              </div>
              <Tooltip title="查看可用分析能力">
                <Button
                  className="fdp-capability-button"
                  icon={<AppstoreOutlined />}
                  onClick={() => openSkillDrawer(true)}
                >
                  {enabledSkills || 0} 项能力
                </Button>
              </Tooltip>
              <Tooltip title="匿名会话已受保护">
                <Button type="text" className="fdp-toolbar-quiet" icon={<SafetyCertificateOutlined />} />
              </Tooltip>
              <Button type="text" className="fdp-toolbar-more" icon={<MoreOutlined />} aria-label="更多" />
            </div>
          </div>
          <ChatWindow />
          <MessageInput />
        </div>
        <SkillManagerDrawer />
      </div>
    </AntdApp>
  );
}
