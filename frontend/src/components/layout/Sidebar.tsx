import { Button, List, Popconfirm, Input, Tooltip, App } from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  AppstoreOutlined,
  ClearOutlined,
} from "@ant-design/icons";
import { useEffect, useState } from "react";
import { useSessionStore } from "../../stores/sessionStore";
import { useChatStore } from "../../stores/chatStore";
import { useSkillStore } from "../../stores/skillStore";
import { api } from "../../lib/api";

export function Sidebar() {
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
  const chatSessionId = useChatStore((s) => s.sessionId);
  const chatSetSession = useChatStore((s) => s.setSession);
  const chatSetMessages = useChatStore((s) => s.setMessages);
  const chatReset = useChatStore((s) => s.reset);

  const skillEnabled = useSkillStore((s) => s.skills.filter((x) => x.enabled).length);
  const skillTotal = useSkillStore((s) => s.skills.length);
  const openSkillDrawer = useSkillStore((s) => s.setDrawerOpen);

  const { message } = App.useApp();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

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
    // We intentionally only run this on mount. Selector-returned
    // action functions are stable references from zustand.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleNew = async () => {
    chatReset();
    setActive(null);
  };

  const handleSelect = async (id: string) => {
    if (activeId === id) return;
    // setSession will internally call _abortInflight, which tears
    // down any in-flight SSE for the previous session. This is the
    // primary fix for the "click a session and the old stream keeps
    // writing into the new one" bug.
    setActive(id);
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
    <div className="fdp-sidebar">
      <div style={{ padding: 12, borderBottom: "1px solid #f0f0f0" }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={handleNew}
          block
          size="large"
        >
          新对话
        </Button>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
        <List
          dataSource={sessions}
          locale={{ emptyText: <div style={{ color: "#bbb", textAlign: "center", padding: 24 }}>暂无对话</div> }}
          renderItem={(s) => (
            <List.Item
              style={{
                padding: "8px 12px",
                cursor: "pointer",
                background: activeId === s.id ? "#e6f4ff" : undefined,
                border: "none",
              }}
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
                <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s.title || "新对话"}
                </div>
              )}
            </List.Item>
          )}
        />
      </div>
      <div style={{ borderTop: "1px solid #f0f0f0", padding: 12 }}>
        <Button
          block
          icon={<AppstoreOutlined />}
          onClick={() => openSkillDrawer(true)}
        >
          Skill 管理 ({skillEnabled}/{skillTotal})
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
              style={{ marginTop: 8 }}
            >
              清空历史
            </Button>
          </Popconfirm>
        )}
      </div>
    </div>
  );
}
