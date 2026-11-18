import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  ComboBox,
  IconButton,
  InlineNotification,
  TextArea,
  TextInput,
} from "@carbon/react";
import { Add, Attachment, Close, Edit, Save, Send } from "@carbon/icons-react";
import { api, streamChat, uploadAttachment } from "../api/client";
import type { Attachment as AttachmentInfo, ChatMessage, ConversationSummary } from "../api/types";
import { Markdown } from "../components/Markdown";
import { EmptyState, LoadError, Loading, PageHeader } from "../components/Ui";
import { useAsync } from "../hooks";
import { navigate, type Route } from "../router";
import { assetUrl, dateTime, errorMessage, FILE_ACCEPT, timeAgo } from "../utils";

function Lightbox({ url, onClose }: { url: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="nova-lightbox" role="dialog" aria-modal="true" aria-label="Image preview" onClick={onClose}>
      <img src={url} alt="" />
      <button type="button" className="nova-lightbox-close" aria-label="Close image preview" onClick={onClose}>
        <Close size={20} />
      </button>
    </div>
  );
}

interface PendingAttachment extends AttachmentInfo {
  localName?: string;
}

const MODELS = [
  { id: "vellur-chat", label: "vellur-chat · general" },
  { id: "vellur-coder", label: "vellur-coder · coding" },
  { id: "vellur-max", label: "vellur-max · hardest problems" },
  { id: "vellur-image", label: "vellur-image · image generation" },
];

export function ChatView({ route }: { route: Route }) {
  const conversations = useAsync(() => api.conversations(), []);
  const llm = useAsync(() => api.llm(), []);
  const [selected, setSelected] = useState<number | null>(route.param ? Number(route.param) : null);
  const [detail, setDetail] = useState<{ id: number; title: string; model: string; messages: ChatMessage[] } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [draft, setDraft] = useState("");
  const [model, setModel] = useState("vellur-chat");
  const [pending, setPending] = useState<PendingAttachment[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [sendError, setSendError] = useState("");
  const [filter, setFilter] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const list = conversations.data?.conversations ?? [];

  useEffect(() => {
    if (route.param) setSelected(Number(route.param));
  }, [route.param]);

  useEffect(() => {
    if (selected == null && list.length > 0) setSelected(list[0].id);
  }, [selected, list]);

  const loadDetail = useCallback(async (id: number) => {
    setDetailLoading(true);
    setDetailError("");
    try {
      const data = await api.conversation(id);
      setDetail({ id: data.id, title: data.title, model: data.model, messages: data.messages ?? [] });
      setModel(data.model || "vellur-chat");
    } catch (reason) {
      setDetailError(errorMessage(reason));
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selected != null) void loadDetail(selected);
    else setDetail(null);
  }, [selected, loadDetail]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [detail?.messages.length, streamText]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? list.filter((item) => item.title.toLowerCase().includes(q)) : list;
  }, [list, filter]);

  const createConversation = async () => {
    try {
      const created = await api.newConversation({ model: "vellur-chat" });
      conversations.reload();
      setSelected(created.id);
      navigate(`chat/${created.id}`);
    } catch (reason) {
      setSendError(errorMessage(reason));
    }
  };

  const removeConversation = async (conversation: ConversationSummary) => {
    if (!window.confirm(`Delete “${conversation.title}”? This removes its messages and attachments.`)) return;
    try {
      await api.deleteConversation(conversation.id);
      if (selected === conversation.id) setSelected(null);
      conversations.reload();
    } catch (reason) {
      setSendError(errorMessage(reason));
    }
  };

  const pickFiles = async (files: FileList | null) => {
    if (!files) return;
    for (const file of Array.from(files)) {
      try {
        const uploaded = await uploadAttachment(file);
        setPending((items) => [...items, { ...uploaded, localName: file.name }]);
      } catch (reason) {
        setSendError(errorMessage(reason));
      }
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || selected == null || streaming) return;
    setSendError("");
    setStreaming(true);
    setStreamText("");
    setDraft("");
    const optimistic: ChatMessage = {
      id: -Date.now(),
      conv_id: selected,
      role: "user",
      content: text,
      created: Date.now() / 1000,
      meta: { attachments: pending },
    };
    setDetail((current) =>
      current ? { ...current, messages: [...current.messages, optimistic] } : current,
    );
    const attachments = pending.map((item) => item.id);
    setPending([]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat(
        selected,
        { content: text, model, attachments },
        {
          onDelta: (delta) => setStreamText((value) => value + delta),
          onError: (message) => setSendError(message),
        },
        controller.signal,
      );
      await loadDetail(selected);
      conversations.reload();
    } catch (reason) {
      if (!controller.signal.aborted) setSendError(errorMessage(reason));
      await loadDetail(selected);
    } finally {
      abortRef.current = null;
      setStreaming(false);
      setStreamText("");
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setStreaming(false);
  };

  const rename = async () => {
    if (!detail || !titleDraft.trim()) return;
    try {
      await api.renameConversation(detail.id, titleDraft.trim());
      setDetail({ ...detail, title: titleDraft.trim() });
      setRenaming(false);
      conversations.reload();
    } catch (reason) {
      setSendError(errorMessage(reason));
    }
  };

  const editAndResend = (message: ChatMessage) => {
    setDraft(message.content);
    setSendError("");
    document.getElementById("nova-chat-input")?.focus();
  };

  return (
    <div className="nova-view nova-chat-view">
      <PageHeader
        kicker="NOVA · Chat"
        title="Conversations"
        description="Streaming answers through the controller router. Attachments are stored by the controller, never in the browser."
        actions={
          <Button kind="primary" renderIcon={Add} onClick={() => void createConversation()}>
            New conversation
          </Button>
        }
      />
      <div className="nova-chat-layout">
        <aside className="nova-chat-list" aria-label="Conversations">
          <input
            className="nova-filter"
            placeholder="Filter conversations"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Filter conversations"
          />
          {conversations.loading ? <Loading label="Loading conversations" /> : null}
          {conversations.error ? <LoadError message={conversations.error} onRetry={conversations.reload} /> : null}
          <ul className="nova-conv-list">
            {visible.map((conversation) => (
              <li key={conversation.id}>
                <a
                  href={`#/chat/${conversation.id}`}
                  className={`nova-conv${selected === conversation.id ? " is-active" : ""}`}
                  onClick={() => setSelected(conversation.id)}
                >
                  <span className="nova-conv-title">{conversation.title || `Conversation ${conversation.id}`}</span>
                  <span className="nova-conv-meta">
                    {conversation.model || "model"} · {timeAgo(conversation.updated)}
                  </span>
                </a>
                <IconButton
                  label={`Delete ${conversation.title}`}
                  size="sm"
                  kind="ghost"
                  onClick={() => void removeConversation(conversation)}
                >
                  <Close size={16} />
                </IconButton>
              </li>
            ))}
          </ul>
          {!conversations.loading && visible.length === 0 ? (
            <EmptyState title="No conversations" detail="Start one and NOVA will keep the history here." />
          ) : null}
        </aside>

        <section className="nova-chat-panel" aria-label="Conversation">
          {selected == null ? (
            <EmptyState title="Choose a conversation" detail="Or create a new one to talk to the router." />
          ) : detailLoading && !detail ? (
            <Loading label="Loading messages" />
          ) : detailError ? (
            <LoadError message={detailError} onRetry={() => void loadDetail(selected)} />
          ) : detail ? (
            <>
              <div className="nova-chat-head">
                <div className="nova-chat-title">
                  {renaming ? (
                    <div className="nova-row">
                      <TextInput
                        id="chat-title"
                        labelText="Conversation title"
                        hideLabel
                        value={titleDraft}
                        maxLength={200}
                        onChange={(event) => setTitleDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") void rename();
                          if (event.key === "Escape") setRenaming(false);
                        }}
                      />
                      <IconButton label="Save title" size="sm" kind="ghost" onClick={() => void rename()}>
                        <Save size={16} />
                      </IconButton>
                    </div>
                  ) : (
                    <h2>
                      {detail.title}
                      <IconButton
                        label="Rename conversation"
                        size="sm"
                        kind="ghost"
                        onClick={() => {
                          setTitleDraft(detail.title);
                          setRenaming(true);
                        }}
                      >
                        <Edit size={16} />
                      </IconButton>
                    </h2>
                  )}
                  <span>
                    {detail.messages.length} messages · {detail.model}
                  </span>
                </div>
                <ComboBox
                  id="chat-model"
                  titleText="Model"
                  items={MODELS}
                  itemToString={(item) => (item ? item.label : "")}
                  selectedItem={MODELS.find((item) => item.id === model) ?? MODELS[0]}
                  onChange={({ selectedItem }) => {
                    if (selectedItem) setModel(selectedItem.id);
                  }}
                />
              </div>
              {llm.error ? <InlineNotification kind="warning" lowContrast title="Model catalog" subtitle={llm.error} hideCloseButton /> : null}
              <div className="nova-messages" ref={scrollRef} aria-live="polite">
                {detail.messages.map((message) => (
                  <article key={message.id} className={`nova-message is-${message.role}`}>
                    <span className="nova-message-role">{message.role === "user" ? "You" : message.role}</span>
                    <div className="nova-message-text">{message.role === "user" ? message.content : <Markdown text={message.content || ""} />}</div>
                    {message.meta?.attachments?.length ? (
                      <div className="nova-attach-list">
                        {message.meta.attachments.map((attachment) => {
                          const url = assetUrl(`/autopilot/api/attachments/${attachment.id}/content`);
                          const isImage = attachment.mime?.startsWith("image/") || /\.(png|jpe?g|gif|webp|avif|bmp)$/i.test(attachment.name || "");
                          return (
                            <a
                              key={attachment.id}
                              href={url}
                              target="_blank"
                              rel="noreferrer"
                              className="nova-attachment"
                              download={attachment.name || undefined}
                            >
                              {isImage ? (
                                <img
                                  className="nova-attachment-thumb"
                                  src={url}
                                  alt={attachment.name || "Attachment"}
                                  onClick={(event) => {
                                    event.preventDefault();
                                    event.stopPropagation();
                                    setZoom(url);
                                  }}
                                />
                              ) : (
                                <Attachment size={14} />
                              )}
                              {attachment.name || attachment.id}
                            </a>
                          );
                        })}
                      </div>
                    ) : null}
                    {Array.isArray(message.meta?.images)
                      ? message.meta.images.map((image, index) => {
                          const url = assetUrl(image.url || (image.id ? `/autopilot/api/images/${image.id}/content` : ""));
                          return (
                            <img
                              key={image.id || index}
                              className="nova-message-image is-zoomable"
                              src={url}
                              alt={image.alt || "Generated image"}
                              role="button"
                              aria-label="View full size"
                              onClick={() => setZoom(url)}
                            />
                          );
                        })
                      : null}
                    <small className="nova-message-time">
                      {dateTime(message.created)}
                      {message.role === "user" ? (
                        <button type="button" className="nova-message-action" onClick={() => editAndResend(message)}>
                          Edit and resend
                        </button>
                      ) : null}
                    </small>
                  </article>
                ))}
                {streaming ? (
                  <article className="nova-message is-assistant is-streaming">
                    <span className="nova-message-role">assistant</span>
                    <div className="nova-message-text">
                      {streamText ? <Markdown text={streamText} /> : <span className="nova-typing">Waiting for the first token…</span>}
                    </div>
                  </article>
                ) : null}
              </div>
              {sendError ? <InlineNotification kind="error" lowContrast title="Message failed" subtitle={sendError} hideCloseButton /> : null}
              {pending.length ? (
                <div className="nova-pending">
                  {pending.map((file) => (
                    <span key={file.id} className="nova-chip">
                      <Attachment size={14} /> {file.localName || file.name}
                      <button
                        type="button"
                        aria-label={`Remove ${file.localName || file.name}`}
                        onClick={() => setPending((items) => items.filter((item) => item.id !== file.id))}
                      >
                        <Close size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="nova-composer">
                <TextArea
                  id="nova-chat-input"
                  labelText="Message"
                  hideLabel
                  rows={3}
                  placeholder="Ask NOVA, paste a stack trace, or attach a file…"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                />
                <div className="nova-composer-actions">
                  <input
                    ref={fileRef}
                    type="file"
                    accept={FILE_ACCEPT}
                    multiple
                    hidden
                    onChange={(event) => void pickFiles(event.target.files)}
                  />
                  <Button kind="ghost" size="sm" renderIcon={Attachment} onClick={() => fileRef.current?.click()}>
                    Attach
                  </Button>
                  {streaming ? (
                    <Button kind="danger" size="sm" onClick={stop}>
                      Stop
                    </Button>
                  ) : (
                    <Button kind="primary" size="sm" renderIcon={Send} disabled={!draft.trim()} onClick={() => void send()}>
                      Send
                    </Button>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </section>
      </div>
      <p className="nova-note">
        Conversations can be renamed and user messages reopened in the composer for an edited resend. The server keeps
        the original turn in history; true in-place message rewriting is not exposed by the API. Click any image to
        view it full size.
      </p>
      {zoom ? <Lightbox url={zoom} onClose={() => setZoom(null)} /> : null}
    </div>
  );
}
