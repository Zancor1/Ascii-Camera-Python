"""Exibe continuamente a webcam como arte ASCII no terminal do Windows."""

from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass

import cv2
import numpy as np

if sys.platform == "win32":
    import msvcrt


CAMERA_INDEX = 0
MAXIMUM_CAMERA_INDEX_TO_SCAN = 9
OUTPUT_WIDTH = 160
OUTPUT_HEIGHT = 90
CHARACTER_ASPECT_RATIO = 0.50
FRAME_DELAY_SECONDS = 0.001
CONTRAST = 1.20
BRIGHTNESS = 8.0
ASCII_CHARACTER_SET = b"@$B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/|()1{}[]?-_+~<>i!lI;:,\"^`'. "


class Console:
    """Ativa ANSI no Windows e escreve cada quadro como um único bloco de bytes."""

    _STD_OUTPUT_HANDLE = -11
    _ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

    def __init__(self) -> None:
        self._enabled = False

    def __enter__(self) -> Console:
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(self._STD_OUTPUT_HANDLE)
            mode = ctypes.c_uint32()

            if handle in (0, -1) or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                raise RuntimeError("Execute o programa em um terminal interativo do Windows.")

            ansi_mode = mode.value | self._ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if not kernel32.SetConsoleMode(handle, ansi_mode):
                raise RuntimeError("Não foi possível ativar códigos ANSI neste terminal.")

        self._enabled = True
        self.write(b"\x1b[2J\x1b[H\x1b[?25l")
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        if self._enabled:
            self.write(b"\x1b[0m\x1b[?25h\n")

    @staticmethod
    def write(content: bytes) -> None:
        """Escreve dados sem conversão Unicode e faz apenas um flush por quadro."""
        sys.stdout.buffer.write(content)
        sys.stdout.buffer.flush()


@dataclass
class FrameTimer:
    """Calcula FPS a partir de uma janela de aproximadamente um segundo."""

    _sample_start: float = time.perf_counter()
    _frames: int = 0
    frames_per_second: float = 0.0

    def tick(self) -> None:
        self._frames += 1
        now = time.perf_counter()
        elapsed = now - self._sample_start

        if elapsed >= 1.0:
            self.frames_per_second = self._frames / elapsed
            self._frames = 0
            self._sample_start = now


class AsciiRenderer:
    """Converte um quadro BGR OpenCV em bytes ASCII sem imprimir pixel por pixel."""

    def __init__(
        self,
        maximum_width: int,
        maximum_height: int,
        character_aspect_ratio: float,
        contrast: float,
        brightness: float,
        characters: bytes,
    ) -> None:
        if maximum_width <= 0 or maximum_height <= 0 or character_aspect_ratio <= 0.0:
            raise ValueError("A resolução e a proporção dos caracteres devem ser positivas.")
        if not characters:
            raise ValueError("O conjunto de caracteres ASCII não pode estar vazio.")

        self.maximum_width = maximum_width
        self.maximum_height = maximum_height
        self.character_aspect_ratio = character_aspect_ratio
        self.contrast = contrast
        self.brightness = brightness
        self.output_width = 0
        self.output_height = 0

        # A LUT mapeia cada brilho possível diretamente para o byte ASCII correspondente.
        brightness_values = np.arange(256, dtype=np.float32)
        adjusted_values = np.clip(brightness_values * contrast + brightness, 0, 255).astype(np.uint8)
        character_indexes = adjusted_values.astype(np.uint16) * (len(characters) - 1) // 255
        self._ascii_lookup = np.frombuffer(characters, dtype=np.uint8)[character_indexes]

    def render(self, bgr_frame: np.ndarray) -> bytes:
        """Retorna linhas ASCII já terminadas por quebra de linha."""
        self._update_output_size(bgr_frame.shape[1], bgr_frame.shape[0])

        gray_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
        resized_frame = cv2.resize(
            gray_frame,
            (self.output_width, self.output_height),
            interpolation=cv2.INTER_AREA,
        )

        # A indexação NumPy converte todos os pixels em caracteres de uma única vez.
        character_pixels = self._ascii_lookup[resized_frame]
        return b"\n".join(character_pixels[row].tobytes() for row in range(self.output_height)) + b"\n"

    def _update_output_size(self, source_width: int, source_height: int) -> None:
        """Mantém a proporção da imagem considerando a largura visual de uma célula de texto."""
        source_aspect = source_width / source_height
        proportional_height = round(self.maximum_width * self.character_aspect_ratio / source_aspect)

        if proportional_height <= self.maximum_height:
            self.output_width = self.maximum_width
            self.output_height = max(1, proportional_height)
        else:
            self.output_width = max(
                1,
                round(self.maximum_height * source_aspect / self.character_aspect_ratio),
            )
            self.output_height = self.maximum_height


def open_camera(index: int) -> cv2.VideoCapture:
    """Tenta os backends de webcam mais confiáveis do Windows e depois o modo automático."""
    backends = (
        (cv2.CAP_DSHOW, "DirectShow"),
        (cv2.CAP_MSMF, "Media Foundation"),
        (cv2.CAP_ANY, "automático"),
    )

    for backend, _backend_name in backends:
        camera = cv2.VideoCapture(index, backend)
        if camera.isOpened():
            return camera
        camera.release()

    raise RuntimeError(
        f"Não foi possível abrir a câmera de índice {index}. "
        "Verifique se ela está conectada, se outro aplicativo não a está usando e se "
        "Configurações > Privacidade e segurança > Câmera permite acesso para aplicativos de desktop."
    )


def find_available_cameras(maximum_index: int) -> list[int]:
    """Retorna os índices de câmera que o OpenCV realmente conseguiu abrir."""
    available_indexes: list[int] = []

    set_opencv_log_level(0)
    try:
        for index in range(maximum_index + 1):
            try:
                camera = open_camera(index)
            except RuntimeError:
                continue

            available_indexes.append(index)
            camera.release()
    finally:
        set_opencv_log_level(3)

    return available_indexes


def set_opencv_log_level(level: int) -> None:
    """Configura logs quando a versão instalada do OpenCV oferece essa API."""
    direct_setter = getattr(cv2, "setLogLevel", None)
    if callable(direct_setter):
        direct_setter(level)
        return

    utilities = getattr(cv2, "utils", None)
    logging_module = getattr(utilities, "logging", None)
    legacy_setter = getattr(logging_module, "setLogLevel", None)
    if callable(legacy_setter):
        legacy_setter(level)


def choose_camera() -> int:
    """Mostra as webcams detectadas e aceita somente um índice disponível."""
    print("Procurando câmeras conectadas...\n")
    available_indexes = find_available_cameras(MAXIMUM_CAMERA_INDEX_TO_SCAN)

    if not available_indexes:
        raise RuntimeError(
            "Nenhuma câmera acessível foi encontrada. Verifique a conexão, as permissões de "
            "câmera do Windows e se outro aplicativo não está usando a webcam."
        )

    print("Câmeras disponíveis para o OpenCV:")
    for index in available_indexes:
        default_marker = " (padrão)" if index == CAMERA_INDEX else ""
        print(f"  [{index}] Câmera de índice {index}{default_marker}")

    default_index = CAMERA_INDEX if CAMERA_INDEX in available_indexes else available_indexes[0]
    while True:
        answer = input(f"\nEscolha o índice da câmera [{default_index}]: ").strip()
        if not answer:
            return default_index

        try:
            selected_index = int(answer)
        except ValueError:
            print("Digite somente um dos índices exibidos.")
            continue

        if selected_index in available_indexes:
            return selected_index
        print("Esse índice não está na lista de câmeras disponíveis.")


def quit_requested() -> bool:
    if sys.platform != "win32" or not msvcrt.kbhit():
        return False
    return msvcrt.getch().lower() == b"q"


def main() -> int:
    """Executa o ciclo de captura, conversão e escrita do vídeo ASCII."""
    renderer = AsciiRenderer(
        OUTPUT_WIDTH,
        OUTPUT_HEIGHT,
        CHARACTER_ASPECT_RATIO,
        CONTRAST,
        BRIGHTNESS,
        ASCII_CHARACTER_SET,
    )
    timer = FrameTimer()
    selected_camera_index = choose_camera()
    camera = open_camera(selected_camera_index)

    try:
        with Console() as console:
            while True:
                success, frame = camera.read()
                if not success or frame is None:
                    raise RuntimeError(f"Falha ao capturar um quadro da câmera {CAMERA_INDEX}.")

                ascii_frame = renderer.render(frame)
                timer.tick()
                header = (
                    f"\x1b[H"
                    f"FPS: {timer.frames_per_second:.0f}\n"
                    f"Resolution: {renderer.output_width}x{renderer.output_height}\n"
                    f"Camera: {selected_camera_index}\n"
                    f"Press Q to Quit\n\n"
                ).encode("ascii")
                console.write(header + ascii_frame)

                if quit_requested():
                    break

                if FRAME_DELAY_SECONDS > 0.0:
                    time.sleep(FRAME_DELAY_SECONDS)
    finally:
        camera.release()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nEncerrado pelo ctrl c.")
        raise SystemExit(0) from None
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
