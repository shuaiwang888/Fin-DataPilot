import { App as AntdApp } from "antd";
import { Sidebar } from "./components/layout/Sidebar";
import { SkillManagerDrawer } from "./components/layout/SkillManagerDrawer";
import { ChatWindow } from "./components/chat/ChatWindow";
import { MessageInput } from "./components/chat/MessageInput";

export default function App() {
  return (
    <AntdApp>
      <div className="fdp-app">
        <Sidebar />
        <div className="fdp-main">
          <div className="fdp-toolbar">
            <strong>Fin-DataPilot</strong>
            <span style={{ color: "#999", fontSize: 12 }}>· 自然语言金融数据 Agent</span>
          </div>
          <ChatWindow />
          <MessageInput />
        </div>
        <SkillManagerDrawer />
      </div>
    </AntdApp>
  );
}
