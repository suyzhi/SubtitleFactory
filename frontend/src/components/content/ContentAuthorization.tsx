interface Props {
  granted: boolean;
  busy: boolean;
  onGrant: () => void;
}

export default function ContentAuthorization({ granted, busy, onGrant }: Props) {
  if (granted) return null;
  return (
    <section className="content-authorization">
      <span>云端内容生成尚未授权</span>
      <div>
        <strong>只发送生成所需的文字</strong>
        <p>
          会上传当前项目标题、所选字幕文字、时间码和说话人标签。不会上传视频、音频、本机路径或其他项目内容。
        </p>
      </div>
      <button className="button primary" disabled={busy} onClick={onGrant}>
        {busy ? '正在保存…' : '阅读并授权'}
      </button>
    </section>
  );
}
