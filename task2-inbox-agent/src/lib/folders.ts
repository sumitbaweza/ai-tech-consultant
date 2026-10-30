// The five Gmail folders, in the order Gmail's left rail shows them
export const FOLDERS = ["inbox", "drafts", "sent", "spam", "trash"] as const;

export type Folder = (typeof FOLDERS)[number];

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
