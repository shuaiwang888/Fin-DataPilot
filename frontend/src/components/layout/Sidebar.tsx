import { Button, List, Popconfirm, Input, Tooltip, App, Modal, Tag } from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  AppstoreOutlined,
  ClearOutlined,
  BulbOutlined,
  BankOutlined,
  MessageOutlined,
  ClockCircleOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { useEffect, useState } from "react";
import { useSessionStore } from "../../stores/sessionStore";
import { useChatStore } from "../../stores/chatStore";
import { useSkillStore } from "../../stores/skillStore";
import { api } from "../../lib/api";
import type { MemoryItem } from "../../lib/api";

interface SidebarProps {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}

function formatSessionTime(value: string) {
  const date = new Date(value);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

export function Sidebar({ mobileOpen = false, onMobileClose }: SidebarProps) {
  // Selector-based subscriptions so streaming token updates (which
  // mutate chatStore.messages / pendingText every frame) don't
  // re-render the entire session list. Previously `useChatStore()`
  // pulled the whole object, causing 30+ <List.Item> nodes to
  // re-render on every token.
  const sessions = useSessionStore((s) => s.sessions);
  const activeId = useSessionStore((s) => s.activeId);
  const setActive = useSessionStore((s) => s.setActive);
  const setSessions = useSessionStore((s) => s.setSessions);
  const removeSession = useSessionStore((s) => s.remove);
  const renameSession = useSessionStore((s) => s.rename);

  // chatStore: only the actions + the id, not the streaming state.
  // Each subscription is a separate selector call so each one is
  // compared with Object.is against the previous value — actions
  // are stable references from zustand, sessionId is a primitive.
  const chatSetSession = useChatStore((s) => s.setSession);
  const chatSetMessages = useChatStore((s) => s.setMessages);
  const chatReset = useChatStore((s) => s.reset);

  const skillEnabled = useSkillStore((s) => s.skills.filter((x) => x.enabled).length);
  const skillTotal = useSkillStore((s) => s.skills.length);
  const openSkillDrawer = useSkillStore((s) => s.setDrawerOpen);

  const { message } = App.useApp();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [memoryLoading, setMemoryLoading] = useState(false);

  const openMemories = async () => {
    setMemoryOpen(true);
    setMemoryLoading(true);
    try {
      const result = await api.listMemories();
      setMemories(result.memories);
    } catch (e) {
      message.error("加载记忆失败：" + (e as Error).message);
    } finally {
      setMemoryLoading(false);
    }
  };

  const removeMemory = async (id: string) => {
    await api.deleteMemory(id);
    setMemories((items) => items.filter((item) => item.id !== id));
  };

  const clearMemories = async () => {
    const result = await api.clearMemories();
    setMemories([]);
    message.success(
      `已清除 ${result.deleted.long_term} 条长期记忆和 ${result.deleted.short_term} 条短期摘要`,
    );
  };

  useEffect(() => {
    // Race guard: if Sidebar unmounts (e.g. test cleanup, future
    // routing) or a faster re-mount races the first request,
    // abort the in-flight list/getSession calls.
    const ctrl = new AbortController();
    (async () => {
      try {
        const r = await api.listSessions(ctrl.signal);
        setSessions(r.sessions);
        // On page load, if the user has past conversations, auto-select
        // the most recent one and load its messages so the page
        // refresh doesn't wipe their history.
        const currentChatId = useChatStore.getState().sessionId;
        if (r.sessions.length > 0 && !useSessionStore.getState().activeId && !currentChatId) {
          const mostRecent = r.sessions[0];
          setActive(mostRecent.id);
          chatSetSession(mostRecent.id);
          try {
            const detail = await api.getSession(mostRecent.id, ctrl.signal);
            chatSetMessages(detail.messages);
          } catch (e) {
            if ((e as Error)?.name !== "AbortError") {
              /* non-fatal */
            }
          }
        }
      } catch (e) {
        if ((e as Error)?.name !== "AbortError") {
          /* offline / not yet deployed; ignore */
        }
      }
    })();
    return () => ctrl.abort();
  }, []);

  const handleNew = async () => {
    chatReset();
    setActive(null);
    onMobileClose?.();
  };

  const handleSelect = async (id: string) => {
    if (activeId === id) return;
    // setSession will internally call _abortInflight, which tears
    // down any in-flight SSE for the previous session. This is the
    // primary fix for the "click a session and the old stream keeps
    // writing into the new one" bug.
    setActive(id);
    onMobileClose?.();
    chatSetSession(id);
    const ctrl = new AbortController();
    try {
      const r = await api.getSession(id, ctrl.signal);
      chatSetMessages(r.messages);
    } catch (e) {
      if ((e as Error)?.name !== "AbortError") {
        console.error(e);
      }
    }
    // Hold a ref to ctrl so we could abort on unmount, but in practice
    // the click handler's await finishes within a few hundred ms;
    // a hot switch to yet another session will set sessionId again,
    // and the for-await sessionId-guard in useChatStream handles
    // the SSE half. The HTTP half is short-lived and self-contained.
    void ctrl;
  };

  const handleDelete = async (id: string) => {
    await api.deleteSession(id);
    removeSession(id);
    if (activeId === id) {
      chatReset();
    }
  };

  const handleClearAll = async () => {
    try {
      const r = await api.deleteAllSessions();
      setSessions([]);
      chatReset();
      message.success(`已清空 ${r.deleted} 条对话历史`);
    } catch (e) {
      message.error("清空失败：" + (e as Error).message);
    }
  };

  const handleRename = async (id: string) => {
    if (!editingTitle.trim()) return;
    await api.patchSession(id, editingTitle.trim());
    renameSession(id, editingTitle.trim());
    setEditingId(null);
  };

  return (
    <aside className={`fdp-sidebar ${mobileOpen ? "is-mobile-open" : ""}`}>
      <div className="fdp-brand">
        <div className="fdp-brand-mark"><BankOutlined /></div>
        <div className="fdp-brand-name">
          <strong>Fin DataPilot</strong>
          <span>AI RESEARCH WORKSPACE</span>
        </div>
      </div>
      <div className="fdp-new-chat-wrap">
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={handleNew}
          block
          size="large"
          className="fdp-new-chat"
        >
          新建分析
        </Button>
      </div>
      <div className="fdp-sidebar-section-head">
        <span><ClockCircleOutlined /> 最近会话</span>
        <span className="fdp-session-count">{sessions.length}</span>
      </div>
      <div className="fdp-session-list">
        <List
          dataSource={sessions}
          locale={{ emptyText: <div className="fdp-empty-sessions"><MessageOutlined /><span>暂无分析记录</span></div> }}
          renderItem={(s) => (
            <List.Item
              className={`fdp-session-item ${activeId === s.id ? "is-active" : ""}`}
              onClick={() => editingId !== s.id && handleSelect(s.id)}
              actions={
                editingId === s.id
                  ? [
                      <Button
                        key="save"
                        type="link"
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRename(s.id);
                        }}
                      >
                        保存
                      </Button>,
                    ]
                  : [
                      <Tooltip title="重命名" key="edit">
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingId(s.id);
                            setEditingTitle(s.title);
                          }}
                        />
                      </Tooltip>,
                      <Popconfirm
                        key="del"
                        title="删除这个对话？"
                        onConfirm={(e) => {
                          e?.stopPropagation();
                          handleDelete(s.id);
                        }}
                        onCancel={(e) => e?.stopPropagation()}
                      >
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Popconfirm>,
                    ]
              }
            >
              {editingId === s.id ? (
                <Input
                  value={editingTitle}
                  autoFocus
                  onChange={(e) => setEditingTitle(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onPressEnter={() => handleRename(s.id)}
                  size="small"
                />
              ) : (
                <div className="fdp-session-content">
                  <span className="fdp-session-title">{s.title || "新对话"}</span>
                  <span className="fdp-session-time">{formatSessionTime(s.updated_at || s.created_at)}</span>
                </div>
              )}
            </List.Item>
          )}
        />
      </div>
      <div className="fdp-sidebar-footer">
        <div className="fdp-sidebar-footer-label">工作区</div>
        <Button block type="text" icon={<BulbOutlined />} onClick={openMemories}>
          记忆管理
        </Button>
        <Button
          block
          type="text"
          icon={<AppstoreOutlined />}
          onClick={() => openSkillDrawer(true)}
        >
          <span>分析能力</span>
          <span className="fdp-skill-count">{skillEnabled}/{skillTotal}</span>
        </Button>
        {sessions.length > 0 && (
          <Popconfirm
            title="清空全部对话历史？"
            description="此操作不可撤销，所有对话将被永久删除。"
            okText="清空"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={handleClearAll}
          >
            <Button
              block
              type="text"
              danger
              size="small"
              icon={<ClearOutlined />}
            >
              清空历史
            </Button>
          </Popconfirm>
        )}
        <div className="fdp-profile-card">
          <div className="fdp-profile-avatar">DP</div>
          <div className="fdp-profile-copy"><strong>本地研究员</strong><span>匿名安全会话</span></div>
          <SettingOutlined />
        </div>
      </div>
      <Modal
        title="记忆管理"
        open={memoryOpen}
        onCancel={() => setMemoryOpen(false)}
        footer={
          <Popconfirm
            title="清除全部记忆？"
            description="将删除长期记忆和所有会话摘要，不会删除聊天记录。"
            onConfirm={clearMemories}
            okButtonProps={{ danger: true }}
          >
            <Button danger>清除全部记忆</Button>
          </Popconfirm>
        }
      >
        <div style={{ color: "#777", fontSize: 12, marginBottom: 12 }}>
          记忆仅绑定当前浏览器的匿名身份；清除浏览器数据或更换设备后无法找回。
        </div>
        <List
          loading={memoryLoading}
          dataSource={memories}
          locale={{ emptyText: "暂无长期记忆" }}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button key="delete" type="link" danger onClick={() => removeMemory(item.id)}>
                  删除
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={<Tag>{item.category}</Tag>}
                description={item.content}
              />
            </List.Item>
          )}
        />
      </Modal>
    </aside>
  );
}
