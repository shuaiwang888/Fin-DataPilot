import {
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Space,
  Switch,
  Tag,
  Upload,
  message,
} from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { useState } from "react";
import { useSkillStore } from "../../stores/skillStore";
import { api } from "../../lib/api";
import type { SkillItem } from "../../lib/types";

const { Dragger } = Upload;
// This public SPA has only an anonymous bearer identity. It must never try to
// act as the operator or expose an ADMIN_API_KEY in a VITE_* variable.
const CAN_MANAGE_SKILLS = false;

export function SkillManagerDrawer() {
  const skills = useSkillStore();
  const [debugInputs, setDebugInputs] = useState<Record<string, Record<string, string>>>({});
  const [debugResult, setDebugResult] = useState<Record<string, unknown>>({});
  const [uploading, setUploading] = useState(false);
  const [msg, ctx] = message.useMessage();

  const handleToggle = async (s: SkillItem, enabled: boolean) => {
    try {
      await api.toggleSkill(s.spec.name, enabled);
      skills.setEnabled(s.spec.name, enabled);
    } catch (e) {
      msg.error("切换失败：" + (e as Error).message);
    }
  };

  const handleDebug = async (s: SkillItem) => {
    const raw = debugInputs[s.spec.name] ?? {};
    const args: Record<string, unknown> = {};
    for (const p of s.spec.parameters) {
      const v = raw[p.name];
      if (v) args[p.name] = v;
    }
    try {
      const r = await api.debugSkill(s.spec.name, args);
      setDebugResult({ ...debugResult, [s.spec.name]: r });
      msg.success(`${s.spec.display_name} 调用成功`);
    } catch (e) {
      msg.error("调用失败：" + (e as Error).message);
    }
  };

  const handleDelete = async (s: SkillItem) => {
    try {
      await api.deleteSkill(s.spec.name);
      skills.removeSkill(s.spec.name);
      msg.success(`已删除 skill：${s.spec.display_name}`);
    } catch (e) {
      msg.error("删除失败：" + (e as Error).message);
    }
  };

  return (
    <>
      {ctx}
      <Drawer
        title="Skill 管理"
        open={skills.drawerOpen}
        onClose={() => skills.setDrawerOpen(false)}
        width={560}
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          {CAN_MANAGE_SKILLS && <Dragger
            name="file"
            accept=".zip"
            multiple={false}
            showUploadList={false}
            disabled={uploading}
            beforeUpload={async (file) => {
              setUploading(true);
              try {
                const newSkill = await api.uploadSkill(file);
                skills.addSkill(newSkill);
                msg.success(`已安装 skill：${newSkill.spec.display_name}`);
              } catch (e) {
                msg.error("上传失败：" + (e as Error).message);
              } finally {
                setUploading(false);
              }
              // Prevent AntD's default upload — we already did it ourselves.
              return false;
            }}
            style={{ background: uploading ? "#fafafa" : undefined }}
          >
            <p className="ant-upload-drag-icon" style={{ marginBottom: 4 }}>
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">
              {uploading ? "正在安装…" : "点击或拖入 .zip 文件上传新 Skill"}
            </p>
            <p className="ant-upload-hint" style={{ fontSize: 12, color: "#999" }}>
              仅支持经管理员审核的 prompt-only Skill（≤ 20 MB）。<br />
              代码 Skill 不会在服务进程中执行。
            </p>
          </Dragger>}

          {!CAN_MANAGE_SKILLS && (
            <div style={{ color: "#666", fontSize: 12 }}>
              已发布 Skill 由管理员统一审核、配置和启用；当前账号仅可使用已发布能力。
            </div>
          )}

          {skills.skills.length === 0 ? (
            <Empty description="加载中..." />
          ) : (
            skills.skills.map((s) => (
              <div
                key={s.spec.name}
                style={{
                  border: "1px solid #f0f0f0",
                  borderRadius: 8,
                  padding: 12,
                  background: s.enabled ? "#fff" : "#fafafa",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div>
                    <strong>{s.spec.display_name}</strong>
                    <Tag style={{ marginLeft: 8 }} color={s.enabled ? "green" : "default"}>
                      {s.enabled ? "启用" : "禁用"}
                    </Tag>
                    <Tag color="blue">{s.spec.category}</Tag>
                    <Tag>v{s.spec.version}</Tag>
                    {s.kind === "prompt" && <Tag color="cyan">知识注入</Tag>}
                    {s.kind === "code" && <Tag color="purple">用户代码</Tag>}
                  </div>
                  <Space size={4}>
                    {CAN_MANAGE_SKILLS && s.uploaded && (
                      <Popconfirm
                        title="确定删除？"
                        description={`将移除「${s.spec.display_name}」及其磁盘文件。此操作不可撤销。`}
                        okText="删除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                        onConfirm={() => handleDelete(s)}
                      >
                        <Button danger size="small">删除</Button>
                      </Popconfirm>
                    )}
                    {CAN_MANAGE_SKILLS && <Switch
                      checked={s.enabled}
                      onChange={(v) => handleToggle(s, v)}
                    />}
                  </Space>
                </div>
                <div style={{ color: "#666", fontSize: 12, marginBottom: 8 }}>{s.spec.description}</div>
                {s.spec.parameters.length > 0 && (
                  <div style={{ fontSize: 12, color: "#999", marginBottom: 8 }}>
                    <strong>参数：</strong>
                    {s.spec.parameters.map((p) => (
                      <Tag key={p.name} color={p.required ? "red" : "default"} style={{ marginBottom: 4 }}>
                        {p.name}{!p.required && "?"}: {p.type}
                      </Tag>
                    ))}
                  </div>
                )}
                {s.spec.requires.length > 0 && (
                  <div style={{ fontSize: 12, marginBottom: 8 }}>
                    {(() => {
                      const missing = s.spec.requires.filter(
                        (e) => s.requirements_met?.[e] === false
                      );
                      if (missing.length === 0) {
                        return (
                          <span style={{ color: "#52c41a" }}>
                            ✓ 环境变量已配置 ({s.spec.requires.join(", ")})
                          </span>
                        );
                      }
                      return (
                        <span style={{ color: "#cf1322" }}>
                          ✗ 需要环境变量: {missing.join(", ")}
                        </span>
                      );
                    })()}
                  </div>
                )}
                {CAN_MANAGE_SKILLS && s.enabled && s.spec.parameters.length > 0 && (
                  <Form size="small" layout="inline" style={{ marginTop: 8 }}>
                    {s.spec.parameters.map((p) => (
                      <Form.Item
                        key={p.name}
                        label={p.name + (p.required ? "*" : "?")}
                        style={{ marginBottom: 4, marginRight: 8 }}
                      >
                        <Input
                          placeholder={p.description}
                          value={debugInputs[s.spec.name]?.[p.name] ?? ""}
                          onChange={(e) =>
                            setDebugInputs({
                              ...debugInputs,
                              [s.spec.name]: {
                                ...(debugInputs[s.spec.name] ?? {}),
                                [p.name]: e.target.value,
                              },
                            })
                          }
                          style={{ width: 160 }}
                        />
                      </Form.Item>
                    ))}
                    <Button type="primary" size="small" onClick={() => handleDebug(s)}>
                      测试调用
                    </Button>
                  </Form>
                )}
                {CAN_MANAGE_SKILLS && s.kind === "prompt" && (
                  <Button
                    size="small"
                    style={{ marginTop: 8 }}
                    onClick={() => handleDebug(s)}
                  >
                    查看 prompt 内容
                  </Button>
                )}
                {debugResult[s.spec.name] !== undefined && (
                  <pre
                    style={{
                      marginTop: 8,
                      background: "#fafafa",
                      border: "1px solid #f0f0f0",
                      borderRadius: 4,
                      padding: 8,
                      fontSize: 11,
                      maxHeight: 200,
                      overflow: "auto",
                    }}
                  >
                    {JSON.stringify(debugResult[s.spec.name], null, 2)}
                  </pre>
                )}
              </div>
            ))
          )}
        </Space>
      </Drawer>
    </>
  );
}
