# meeting.md

Widget para Windows 10/11 que grava o áudio do computador (Teams, Meet ou qualquer aplicativo), inclui o microfone opcionalmente e transcreve depois, localmente, para Markdown em português.

## Abrir nesta máquina

O ambiente `.venv` já está preparado. Abra **iniciar.vbs** com dois cliques. A janela abre sem console; arraste pelo nome `meeting.md`. `−` minimiza e `×` fecha. A janela fica sobre os outros aplicativos e pode ser restaurada pela barra de tarefas do Windows.

Se o Windows bloquear arquivos VBS, abra um terminal nesta pasta e execute:

```powershell
.\.venv\Scripts\python.exe app.py
```

## Usar

1. Confira no Windows que a saída de áudio usada por Teams/Meet é a **saída padrão**. Os dispositivos são escolhidos quando a gravação começa.
2. Digite um nome opcional e mantenha **Incluir meu microfone** se quiser registrar sua própria fala. O microfone é a entrada WASAPI padrão do Windows. Use fones para evitar que ele também capture as outras pessoas pelos alto-falantes.
3. Clique em **Iniciar gravação**. Os indicadores mostram o nível recebido. Os arquivos são escritos no disco durante a reunião.
4. Clique em **Parar e salvar áudio** e escolha **Transcrever agora** ou **Deixar para depois**. Ambas retornam à tela principal.
5. Comece outra gravação quando precisar: a transcrição em andamento pausa e retoma depois que o novo áudio for salvo.
6. Abra **Reuniões**, selecione uma reunião e veja suas informações, transcreva uma pendência ou abra o Markdown concluído.

O primeiro uso do transcritor baixa o modelo `base` do Hugging Face (internet necessária). Depois, com o modelo em cache, o processamento é local. Nenhum áudio é enviado ao serviço de transcrição. O modelo só é carregado quando há uma transcrição solicitada.

A estimativa aparece depois da primeira transcrição com pelo menos 20 segundos, usando o tempo medido nesta máquina e a mesma combinação de fontes. Download/carregamento inicial e pausas não entram no cálculo. É uma faixa aproximada, não um prazo garantido.

## Arquivos e recuperação

```text
data/
  models/                         modelo local
  20260906-143000-<id único>/
    meeting.json                  título, ID, horários com fuso, fontes e estado
    computer.wav                  áudio do computador em PCM mono
    microphone.wav                microfone, quando habilitado
    transcription.md              transcrição final com horários e fonte
    progress.json                 progresso do processamento
    transcription.log             detalhes de uma falha
```

Reuniões com o mesmo título têm IDs diferentes. Pedidos repetidos não duplicam trabalhos. Falhas preservam o áudio e permitem tentar novamente. Ao reabrir após uma interrupção, o app recupera o cabeçalho WAV e marca trabalhos interrompidos como pendentes. Não há exclusão automática.

**A pasta atual fica dentro do OneDrive:** os arquivos podem ser sincronizados por ele. Para armazenar fora dessa sincronização, instale o projeto numa pasta local fora do OneDrive. Não mova uma `.venv` pronta: recrie-a no novo local.

## Instalar em outra máquina

Requer Python **3.11, 3.12 ou 3.13, 64 bits**, com Tcl/Tk e pip. Python 3.14 ainda não é atendido pelos pacotes binários de captura usados aqui.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Também é possível executar `instalar.ps1` com uma dessas versões de Python como padrão. O ambiente preparado nesta máquina usa o Python 3.12 do runtime local do Codex; para independência desse runtime, instale seu próprio Python compatível e recrie `.venv`.

## Verificar

Testes de persistência, recuperação, fila sem duplicatas, geração de Markdown, janela e pausa/retomada real de um processo:

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_app
```

Teste opcional de hardware (reproduz uma fala e grava computador e microfone por alguns segundos): gere um WAV com a voz do Windows e execute o teste abaixo. Ele salva resultados em `data/diagnostics`, separados da biblioteca de reuniões.

```powershell
New-Item -ItemType Directory -Force data\diagnostics | Out-Null
$voz = New-Object -ComObject SAPI.SpVoice
$arquivo = New-Object -ComObject SAPI.SpFileStream
$arquivo.Open((Join-Path $PWD 'data\diagnostics\speech.wav'), 3)
$voz.AudioOutputStream = $arquivo
$voz.Speak('Esta é uma reunião de teste. Vamos validar a gravação.') | Out-Null
$arquivo.Close()
.\.venv\Scripts\python.exe test_hardware.py
```

## Limites desta primeira versão

- Captura toda a saída padrão, incluindo notificações. Não isola Teams/Meet e não acompanha a troca de dispositivo no meio de uma gravação. Pare e inicie outra gravação após trocar o dispositivo.
- Computador e microfone são preservados e transcritos separadamente; os trechos são ordenados pelo horário. A fonte não identifica participantes individuais. Eco capturado no microfone pode repetir falas; use fones.
- Usa CPU, modelo `base` e português. Precisão varia com ruído, sotaque e termos técnicos. Não inclui resumo automático nem identificação de pessoas.
- Gravação e interface são leves em relação à transcrição; o processo de transcrição usa mais memória. Pausar libera a CPU, mas mantém o modelo na memória para retomar.
- Áudio PCM usa cerca de 350 MB por hora por fonte a 48 kHz. Há um limite de 10 horas por reunião/fonte para permanecer dentro do formato WAV clássico.
- Fechar durante a gravação é bloqueado até salvar. Fechar durante uma transcrição pede confirmação e deixa o trabalho pendente; ao tentar novamente, ele reinicia a transcrição desse áudio.

Referências de implementação: [PyAudioWPatch / WASAPI](https://github.com/s0d3s/PyAudioWPatch) e [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
