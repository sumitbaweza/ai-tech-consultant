// The five Gmail folders, in the order Gmail's left rail shows them
export const FOLDERS = ["inbox", "drafts", "sent", "spam", "trash"] as const;

export type Folder = (typeof FOLDERS)[number];

// What the database stores: the five, plus mail Gmail keeps in no folder ("Archive" moves it
// to All Mail). Archived emails stay, because drafts and learning will point at them, but no
// folder lists them. New values only go at the end: Postgres can't reorder an enum.
export const STORED_FOLDERS = [...FOLDERS, "archived"] as const;

export type StoredFolder = (typeof STORED_FOLDERS)[number];

export const FOLDER_LABELS: Record<Folder, string> = {
  inbox: "Inbox",
  drafts: "Drafts",
  sent: "Sent",
  spam: "Spam",
  trash: "Trash",
};

export function isFolder(value: string): value is Folder {
  return (FOLDERS as readonly string[]).includes(value);
}
