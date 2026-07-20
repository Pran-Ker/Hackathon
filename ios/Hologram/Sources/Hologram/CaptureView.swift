import SwiftUI
import PhotosUI
import UniformTypeIdentifiers
import AVKit

struct CaptureView: View {
    let onScan: (CapturePayload) -> Void
    let onDemo: () -> Void

    @State private var pickerItem: PhotosPickerItem?
    @State private var media: PickedMedia?
    @State private var prompt = ""
    @State private var showCamera = false
    @State private var loadingMedia = false
    @State private var loadError: String?
    @FocusState private var promptFocused: Bool

    struct PickedMedia {
        let data: Data
        let filename: String
        let mimeType: String
        let thumbnail: UIImage?
        let isVideo: Bool
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                previewArea

                HStack(spacing: 12) {
                    PhotosPicker(selection: $pickerItem,
                                 matching: .any(of: [.images, .videos])) {
                        Label("Library", systemImage: "photo.on.rectangle")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)

                    if UIImagePickerController.isSourceTypeAvailable(.camera) {
                        Button {
                            showCamera = true
                        } label: {
                            Label("Camera", systemImage: "camera")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                    }
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("What should pop out?")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    TextField("chair, plant, sneaker", text: $prompt)
                        .textFieldStyle(.roundedBorder)
                        .textInputAutocapitalization(.never)
                        .focused($promptFocused)
                        .submitLabel(.go)
                        .onSubmit { scanIfReady() }
                }

                Button {
                    scanIfReady()
                } label: {
                    Label("SCAN", systemImage: "cube.transparent")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .disabled(media == nil || prompt.trimmingCharacters(in: .whitespaces).isEmpty)

                if let loadError {
                    Text(loadError)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }

                Button("Try the demo scene (no server needed)") {
                    onDemo()
                }
                .font(.footnote)
            }
            .padding()
        }
        .navigationTitle("Hologram")
        .onChange(of: pickerItem) { _, item in
            guard let item else { return }
            loadPicked(item)
        }
        .fullScreenCover(isPresented: $showCamera) {
            CameraPicker { image in
                guard let image, let jpeg = image.jpegData(compressionQuality: 0.9) else { return }
                media = PickedMedia(data: jpeg,
                                    filename: "capture.jpg",
                                    mimeType: "image/jpeg",
                                    thumbnail: image,
                                    isVideo: false)
            }
            .ignoresSafeArea()
        }
    }

    @ViewBuilder
    private var previewArea: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 16)
                .fill(Color(.secondarySystemBackground))
                .frame(height: 280)

            if loadingMedia {
                ProgressView("Loading…")
            } else if let media {
                if let thumb = media.thumbnail {
                    Image(uiImage: thumb)
                        .resizable()
                        .scaledToFill()
                        .frame(height: 280)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .overlay(alignment: .bottomTrailing) {
                            if media.isVideo {
                                Image(systemName: "video.fill")
                                    .padding(8)
                                    .background(.ultraThinMaterial, in: Circle())
                                    .padding(8)
                            }
                        }
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: media.isVideo ? "video.fill" : "photo")
                            .font(.system(size: 40))
                        Text(media.filename)
                            .font(.caption)
                    }
                    .foregroundStyle(.secondary)
                }
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "sparkles.rectangle.stack")
                        .font(.system(size: 48))
                        .foregroundStyle(.secondary)
                    Text("Take or pick a photo or video")
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func scanIfReady() {
        guard let media else { return }
        let trimmed = prompt.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        promptFocused = false
        onScan(CapturePayload(data: media.data,
                              filename: media.filename,
                              mimeType: media.mimeType,
                              prompt: trimmed))
    }

    private func loadPicked(_ item: PhotosPickerItem) {
        loadingMedia = true
        loadError = nil
        Task {
            defer { loadingMedia = false }
            do {
                guard let data = try await item.loadTransferable(type: Data.self) else {
                    loadError = "Couldn't load that item."
                    return
                }
                let type = item.supportedContentTypes.first
                let isVideo = type?.conforms(to: .movie) ?? false
                let ext = type?.preferredFilenameExtension ?? (isVideo ? "mov" : "jpg")
                let mime = type?.preferredMIMEType ?? (isVideo ? "video/quicktime" : "image/jpeg")
                let thumbnail: UIImage?
                if isVideo {
                    thumbnail = await videoThumbnail(data: data, ext: ext)
                } else {
                    thumbnail = UIImage(data: data)
                }
                media = PickedMedia(data: data,
                                    filename: "upload.\(ext)",
                                    mimeType: mime,
                                    thumbnail: thumbnail,
                                    isVideo: isVideo)
            } catch {
                loadError = "Couldn't load that item: \(error.localizedDescription)"
            }
        }
    }

    private func videoThumbnail(data: Data, ext: String) async -> UIImage? {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("thumb-\(UUID().uuidString).\(ext)")
        do {
            try data.write(to: tmp)
            defer { try? FileManager.default.removeItem(at: tmp) }
            let generator = AVAssetImageGenerator(asset: AVURLAsset(url: tmp))
            generator.appliesPreferredTrackTransform = true
            let (image, _) = try await generator.image(at: .zero)
            return UIImage(cgImage: image)
        } catch {
            return nil
        }
    }
}
