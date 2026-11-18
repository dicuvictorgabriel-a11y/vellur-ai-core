import { useEffect, useState } from "react";
import { Button, InlineNotification, Modal, Select, Tag, TextArea, TextInput } from "@carbon/react";
import { Add, Edit, Music, TrashCan } from "@carbon/icons-react";
import { api } from "../api/client";
import type { MusicRelease, MusicTrack } from "../api/types";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle } from "../components/Ui";
import { useAsync } from "../hooks";
import { navigate, type Route } from "../router";
import { dateOnly, errorMessage, tagType } from "../utils";

type ReleaseForm = {
  title: string;
  artist: string;
  status: string;
  target_date: string;
  released_at: string;
  notes: string;
  cover_image_id: string;
};

function CoverPicker({ onSelect }: { onSelect: (imageId: string) => void }) {
  const [images, setImages] = useState<Array<{ id: string; url: string }> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .listImages()
      .then((list) => setImages(list.images || []))
      .catch((reason) => setError(errorMessage(reason)));
  }, []);

  if (error) return <InlineNotification kind="error" lowContrast title={error} hideCloseButton />;
  if (!images) return <p className="nova-note">Loading images…</p>;
  if (!images.length) return <p className="nova-note">No images in the library yet. Generate one in Chat first.</p>;

  return (
    <ul className="nova-cover-picker">
      {images.map((image) => (
        <li key={image.id}>
          <button type="button" onClick={() => onSelect(image.id)} aria-label={`Use image ${image.id} as cover`}>
            <img src={image.url} alt="" loading="lazy" />
          </button>
        </li>
      ))}
    </ul>
  );
}

const EMPTY_RELEASE: ReleaseForm = {
  title: "",
  artist: "",
  status: "planned",
  target_date: "",
  released_at: "",
  notes: "",
  cover_image_id: "",
};

export function MusicView({ route }: { route: Route }) {
  const music = useAsync(() => api.music(), []);
  const releases = music.data?.releases || [];
  const selectedId = route.param ? Number(route.param) : releases[0]?.id ?? null;
  const selected = releases.find((release) => release.id === selectedId) || null;
  const [dialog, setDialog] = useState(false);
  const [editing, setEditing] = useState<MusicRelease | null>(null);
  const [form, setForm] = useState<ReleaseForm>(EMPTY_RELEASE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [trackTitle, setTrackTitle] = useState("");
  const [trackDuration, setTrackDuration] = useState("");
  const [trackNotes, setTrackNotes] = useState("");
  const [editingTrack, setEditingTrack] = useState<MusicTrack | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerError, setPickerError] = useState("");
  const [profile, setProfile] = useState({ artist_name: "", genre: "", bio: "", links: "" });

  const openNew = () => {
    setEditing(null);
    setForm({ ...EMPTY_RELEASE, artist: String(music.data?.profile.artist_name || "") });
    setError("");
    setDialog(true);
  };

  const openEdit = (release: MusicRelease) => {
    setEditing(release);
    setForm({
      title: release.title,
      artist: release.artist || "",
      status: release.status,
      target_date: release.target_date || "",
      released_at: release.released_at || "",
      notes: release.notes || "",
      cover_image_id: release.cover_image_id || "",
    });
    setError("");
    setDialog(true);
  };

  const save = async () => {
    if (!form.title.trim()) {
      setError("A release needs a title.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (editing) {
        await api.updateRelease(editing.id, form);
        setDialog(false);
        music.reload();
      } else {
        const created = await api.createRelease(form);
        setDialog(false);
        music.reload();
        navigate(`music/${created.id}`);
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const removeRelease = async (release: MusicRelease) => {
    if (!window.confirm(`Delete “${release.title}” and its tracks? This cannot be undone.`)) return;
    try {
      await api.deleteRelease(release.id);
      music.reload();
      navigate("music");
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const resetTrackForm = () => {
    setEditingTrack(null);
    setTrackTitle("");
    setTrackDuration("");
    setTrackNotes("");
  };

  const startEditTrack = (track: MusicTrack) => {
    setEditingTrack(track);
    setTrackTitle(track.title);
    setTrackDuration(track.duration || "");
    setTrackNotes(track.notes || "");
  };

  const saveTrack = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selected || !trackTitle.trim()) return;
    setError("");
    const body = { title: trackTitle.trim(), duration: trackDuration.trim(), notes: trackNotes.trim() };
    try {
      if (editingTrack) {
        await api.updateTrack(editingTrack.id, body);
      } else {
        await api.addTrack(selected.id, body);
      }
      resetTrackForm();
      music.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const removeTrack = async (id: number) => {
    if (!window.confirm("Remove this track?")) return;
    try {
      await api.deleteTrack(id);
      music.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const openProfile = () => {
    setProfile({
      artist_name: String(music.data?.profile.artist_name || ""),
      genre: String(music.data?.profile.genre || ""),
      bio: String(music.data?.profile.bio || ""),
      links: String(music.data?.profile.links || ""),
    });
    setProfileOpen(true);
  };

  const saveProfile = async () => {
    setBusy(true);
    setError("");
    try {
      await api.saveMusicProfile(profile);
      setProfileOpen(false);
      music.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Music"
        title="Releases"
        description="A small label desk for release plans, tracks, covers and notes. Demo data in the preview is simulated."
        actions={
          <>
            <Button kind="ghost" onClick={openProfile}>Artist profile</Button>
            <Button kind="primary" renderIcon={Add} onClick={openNew}>New release</Button>
          </>
        }
      />
      {error && !dialog ? <InlineNotification kind="error" lowContrast title="Music action failed" subtitle={error} hideCloseButton /> : null}
      {music.error ? <LoadError message={music.error} onRetry={music.reload} /> : null}
      {music.loading && !music.data ? <Loading label="Loading releases" /> : null}
      {music.data ? (
        <div className="nova-split">
          <aside className="nova-card nova-project-list" aria-label="Releases">
            <SectionTitle hint={`${releases.length}`}>Catalog</SectionTitle>
            {releases.length === 0 ? <EmptyState title="No releases" detail="Create the first release plan." /> : null}
            <ul className="nova-list compact">
              {releases.map((release) => (
                <li key={release.id}>
                  <a className={`nova-list-row${selected?.id === release.id ? " is-active" : ""}`} href={`#/music/${release.id}`}>
                    <span className="nova-list-main">
                      <strong>{release.title}</strong>
                      <small>{release.artist || "unknown artist"} · {release.tracks?.length ?? 0} tracks</small>
                    </span>
                    <Tag type={tagType(release.status)}>{release.status}</Tag>
                  </a>
                </li>
              ))}
            </ul>
          </aside>
          <section className="nova-card nova-span-2">
            {selected ? (
              <>
                <SectionTitle hint={<Tag type={tagType(selected.status)}>{selected.status}</Tag>}>{selected.title}</SectionTitle>
                <div className="nova-row">
                  <Button size="sm" kind="tertiary" renderIcon={Edit} onClick={() => openEdit(selected)}>Edit release</Button>
                  <Button size="sm" kind="danger--ghost" renderIcon={TrashCan} onClick={() => void removeRelease(selected)}>Delete</Button>
                </div>
                {selected.cover_image_id ? (
                  <span className="nova-cover-wrap">
                    <img
                      className="nova-cover"
                      src={`/autopilot/api/images/${encodeURIComponent(selected.cover_image_id)}/content`}
                      alt={`Cover for ${selected.title}`}
                    />
                    <span className="nova-cover-blend" aria-hidden="true" />
                  </span>
                ) : null}
                <div className="nova-row">
                  <Button
                    size="sm"
                    kind="tertiary"
                    onClick={() => {
                      setPickerError("");
                      api
                        .listImages()
                        .then(() => setPickerOpen(true))
                        .catch((reason) => setPickerError(errorMessage(reason)));
                    }}
                  >
                    Choose cover
                  </Button>
                </div>
                {pickerError ? <InlineNotification kind="error" lowContrast title={pickerError} hideCloseButton /> : null}
                <dl className="nova-kv">
                  <div><dt>Artist</dt><dd>{selected.artist || "—"}</dd></div>
                  <div><dt>Target</dt><dd>{dateOnly(selected.target_date)}</dd></div>
                  <div><dt>Released</dt><dd>{dateOnly(selected.released_at)}</dd></div>
                  <div><dt>Tracks</dt><dd>{selected.tracks?.length ?? 0}</dd></div>
                </dl>
                {selected.notes ? <p>{selected.notes}</p> : null}
                <div className="nova-table-wrap">
                  <table className="nova-table nova-track-table">
                    <thead><tr><th>#</th><th>Track</th><th>Duration</th><th>Notes</th><th aria-label="Actions" /></tr></thead>
                    <tbody>
                      {(selected.tracks || []).map((track, index) => (
                        <tr key={track.id}>
                          <td>{index + 1}</td>
                          <td>{track.title}</td>
                          <td>{track.duration}</td>
                          <td>{track.notes || "—"}</td>
                          <td>
                            <div className="nova-row nova-track-actions">
                              <Button
                                size="sm"
                                kind="ghost"
                                hasIconOnly
                                renderIcon={Edit}
                                iconDescription={`Edit ${track.title}`}
                                onClick={() => startEditTrack(track)}
                              />
                              <Button
                                size="sm"
                                kind="danger--ghost"
                                hasIconOnly
                                renderIcon={TrashCan}
                                iconDescription={`Delete ${track.title}`}
                                onClick={() => void removeTrack(track.id)}
                              />
                            </div>
                          </td>
                        </tr>
                      ))}
                      {(selected.tracks || []).length === 0 ? <tr><td colSpan={5}>No tracks yet.</td></tr> : null}
                    </tbody>
                  </table>
                </div>
                <form className="nova-card nova-form" onSubmit={saveTrack} aria-label={editingTrack ? "Edit track" : "Add track"}>
                  <SectionTitle>{editingTrack ? `Edit “${editingTrack.title}”` : "Add track"}</SectionTitle>
                  <div className="nova-form-grid">
                    <TextInput id="track-title" labelText="Title" value={trackTitle} onChange={(event) => setTrackTitle(event.target.value)} required />
                    <TextInput id="track-duration" labelText="Duration (e.g. 3:21)" value={trackDuration} onChange={(event) => setTrackDuration(event.target.value)} />
                    <TextInput id="track-notes" labelText="Notes" value={trackNotes} onChange={(event) => setTrackNotes(event.target.value)} />
                  </div>
                  <div className="nova-row">
                    <Button type="submit" size="sm" disabled={!trackTitle.trim()}>{editingTrack ? "Save track" : "Add track"}</Button>
                    {editingTrack ? <Button type="button" size="sm" kind="ghost" onClick={resetTrackForm}>Cancel edit</Button> : null}
                  </div>
                </form>
                <p className="nova-note"><Music size={14} /> {music.data.profile.artist_name ? `Artist profile: ${music.data.profile.artist_name}` : "Artist profile is empty."}</p>
                <Modal
                  open={pickerOpen}
                  modalHeading="Choose a cover image"
                  passiveModal
                  onRequestClose={() => setPickerOpen(false)}
                  aria-label="Choose a cover image"
                >
                  <CoverPicker
                    onSelect={(imageId) => {
                      setPickerOpen(false);
                      setBusy(true);
                      void api
                        .updateRelease(selected.id, { cover_image_id: imageId })
                        .then(() => music.reload())
                        .catch((reason) => setPickerError(errorMessage(reason)))
                        .finally(() => setBusy(false));
                    }}
                  />
                </Modal>
              </>
            ) : (
              <EmptyState title="Choose a release" detail="Or create a new one." />
            )}
          </section>
        </div>
      ) : null}

      <Modal
        open={dialog}
        modalHeading={editing ? "Edit release" : "New release"}
        primaryButtonText={busy ? "Saving…" : editing ? "Save" : "Create"}
        secondaryButtonText="Cancel"
        primaryButtonDisabled={busy}
        onRequestClose={() => setDialog(false)}
        onRequestSubmit={() => void save()}
      >
        <div className="nova-form-grid">
          <TextInput id="release-title" labelText="Title" maxLength={200} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} required />
          <TextInput id="release-artist" labelText="Artist" maxLength={200} value={form.artist} onChange={(event) => setForm({ ...form, artist: event.target.value })} />
          <Select id="release-status" labelText="Status" value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })}>
            <option value="planned">Planned</option>
            <option value="released">Released</option>
          </Select>
          <TextInput id="release-target" labelText="Target date" type="date" value={form.target_date} onChange={(event) => setForm({ ...form, target_date: event.target.value })} />
          <TextInput id="release-released" labelText="Released at" type="date" value={form.released_at} onChange={(event) => setForm({ ...form, released_at: event.target.value })} />
          <TextInput id="release-cover" labelText="Cover image id (Library image)" value={form.cover_image_id} onChange={(event) => setForm({ ...form, cover_image_id: event.target.value })} />
        </div>
        <TextArea id="release-notes" labelText="Notes" rows={4} maxLength={4000} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
        {error ? <p className="nova-form-error" role="alert">{error}</p> : null}
      </Modal>

      <Modal
        open={profileOpen}
        modalHeading="Artist profile"
        primaryButtonText={busy ? "Saving…" : "Save profile"}
        secondaryButtonText="Cancel"
        primaryButtonDisabled={busy}
        onRequestClose={() => setProfileOpen(false)}
        onRequestSubmit={() => void saveProfile()}
      >
        <div className="nova-form-grid">
          <TextInput id="profile-name" labelText="Artist name" value={profile.artist_name} onChange={(event) => setProfile({ ...profile, artist_name: event.target.value })} />
          <TextInput id="profile-genre" labelText="Genre" value={profile.genre} onChange={(event) => setProfile({ ...profile, genre: event.target.value })} />
          <TextInput id="profile-links" labelText="Links" value={profile.links} onChange={(event) => setProfile({ ...profile, links: event.target.value })} />
        </div>
        <TextArea id="profile-bio" labelText="Bio" rows={5} value={profile.bio} onChange={(event) => setProfile({ ...profile, bio: event.target.value })} />
        {error ? <p className="nova-form-error" role="alert">{error}</p> : null}
      </Modal>
    </div>
  );
}
