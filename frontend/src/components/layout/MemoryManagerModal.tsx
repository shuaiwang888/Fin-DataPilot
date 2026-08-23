import { useEffect, useState } from "react";
import { App, Button, List, Modal, Popconfirm, Tag } from "antd";
import { api } from "../../lib/api";
import type { MemoryItem } from "../../lib/api";

interface MemoryManagerModalProps {
  open: boolean;
  onClose: () => void;
}

export function MemoryManagerModal({ open, onClose }: MemoryManagerModalProps) {
  const { message } = App.useApp();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    api.listMemories(controller.signal)
      .then((result) => setMemories(result.memories))
      .catch((error: Error) => {
        if (error.name !== "AbortError") message.error("加载记忆失败：" + error.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [message]);

  const removeMemory = async (id: string) => {
    try {
      await api.deleteMemory(id);
      setMemories((items) => items.filter((item) => item.id !== id));
    } catch (error) {
      message.error("删除失败：" + (error as Error).message);
    }
  };

  const clearMemories = async () => {
    try {
      const result = await api.clearMemories();
      setMemories([]);
      message.success(
        `已清除 ${result.deleted.long_term} 条长期记忆和 ${result.deleted.short_term} 条短期摘要`,
      );
    } catch (error) {
      message.error("清除失败：" + (error as Error).message);
    }
  };

  return (
    <Modal
      title="记忆管理"
      open={open}
      onCancel={onClose}
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
        loading={loading}
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
            <List.Item.Meta title={<Tag>{item.category}</Tag>} description={item.content} />
          </List.Item>
        )}
      />
    </Modal>
  );
}
