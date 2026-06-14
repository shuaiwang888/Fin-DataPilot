import { Button, List, Popconfirm, Input, Tooltip } from "antd";
import { PlusOutlined, DeleteOutlined, EditOutlined, AppstoreOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { useSessionStore } from "../../stores/sessionStore";
import { useChatStore } from "../../stores/chatStore";
import { useSkillStore } from "../../stores/skillStore";
import { api } from "../../lib/api";

export function Sidebar() {
  const sessions = useSessionStore();
  const chat = useChatStore();
  const skills = useSkillStore();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const r = await api.listSessions();
        sessions.setSessions(r.sessions);
        // On page load, if the user has past conversations, auto-select
        // the most recent one and load its messages so the page
        // refresh doesn't wipe their history.
        if (r.sessions.length > 0 && !sessions.activeId && !chat.sessionId) {
          const mostRecent = r.sessions[0];
          sessions.setActive(mostRecent.id);
          chat.setSession(mostRecent.id);
          try {
            const detail = await api.getSession(mostRecent.id);
            chat.setMessages(detail.messages);
          } catch {
            /* non-fatal */
          }
        }
      } catch {
        /* offline / not yet deployed; ignore */
      }
    })();
  }, []);

  const handleNew = async () => {
    chat.reset();
    sessions.setActive(null);
  };

  const handleSelect = async (id: string) => {
    if (sessions.activeId === id) return;
    sessions.setActive(id);
    chat.setSession(id);
    try {
      const r = await api.getSession(id);
      chat.setMessages(r.messages);
    } catch (e) {
      console.error(e);
    }
  };

  const handleDelete = async (id: string) => {
    await api.deleteSession(id);
    sessions.remove(id);
    if (sessions.activeId === id) {
      chat.reset();
    }
  };

  const handleRename = async (id: string) => {
    if (!editingTitle.trim()) return;
    await api.patchSession(id, editingTitle.trim());
    sessions.rename(id, editingTitle.trim());
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
          dataSource={sessions.sessions}
          locale={{ emptyText: <div style={{ color: "#bbb", textAlign: "center", padding: 24 }}>暂无对话</div> }}
          renderItem={(s) => (
            <List.Item
              style={{
                padding: "8px 12px",
                cursor: "pointer",
                background: sessions.activeId === s.id ? "#e6f4ff" : undefined,
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
          onClick={() => skills.setDrawerOpen(true)}
        >
          Skill 管理 ({skills.skills.filter((s) => s.enabled).length}/{skills.skills.length})
        </Button>
      </div>
    </div>
  );
}
