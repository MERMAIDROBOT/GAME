// Synthetic host input: no window, device, or retail data.
#include "raylib.h"
#include "runtime/ps2_pad.h"
#include <array>
#include <deque>
#include <utility>
#include <initializer_list>
#include <iostream>
#include <stdexcept>
#include <atomic>
#include <thread>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <regex>
#include <set>
#if defined(__unix__) || defined(__APPLE__)
#include <unistd.h>
#endif

static std::array<bool, 512> down{};
static std::deque<int> presses;
extern "C" {
bool IsGamepadAvailable(int) { return false; }
bool IsGamepadButtonDown(int, int) { return false; }
float GetGamepadAxisMovement(int, int) { return 0; }
bool IsKeyDown(int key) { return down.at(key); }
bool IsMouseButtonDown(int) { return false; }
int GetKeyPressed() {
    if (presses.empty()) return 0;
    int key = presses.front(); presses.pop_front(); return key;
}
}
void ps2PortPollHostInput();
bool ps2PortConsumeControlsToggle();
static PSPadBackend pad;
static std::array<uint8_t,32> read() {
    std::array<uint8_t,32> state{};
    ps2PortPollHostInput();
    if (!pad.readState(0,0,state.data(),state.size())) throw std::runtime_error("Pad read failed");
    return state;
}
static uint16_t buttons(const std::array<uint8_t,32>& s) {
    return static_cast<uint16_t>(~(s[2] | (s[3]<<8)));
}
static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

// Reproduce the real scenario failure: GPU diagnostics can arrive between
// individual stream insertions in a controller ACK. Every ACK must stay parseable.
static void testConcurrentCommandAcknowledgments() {
#if defined(__unix__) || defined(__APPLE__)
    char path[]="/tmp/ps2-port-pad-command-XXXXXX";
    const int commandFd=mkstemp(path);require(commandFd>=0,"Cannot create pad test command");close(commandFd);
    FILE *capture=tmpfile();require(capture,"Cannot create pad test log");
    const int saved=dup(STDERR_FILENO);require(saved>=0,"Cannot preserve stderr");
    fflush(stderr);require(dup2(fileno(capture),STDERR_FILENO)>=0,"Cannot capture stderr");
    setenv("PS2_PORT_HEADLESS","1",1);setenv("PS2_PORT_PAD_COMMAND",path,1);
    std::atomic<bool> stop{false};
    std::thread diagnostics([&]{for(unsigned n=0;n<100000 && !stop.load();++n)
        std::fprintf(stderr,"[concurrent-gpu-field] tick=%u ingress=%u\n",n,n*7);});
    std::array<uint8_t,32> neutral{};neutral[0]=1;neutral[1]=0x73;neutral[2]=neutral[3]=255;
    for(unsigned n=4;n<8;n++)neutral[n]=128;
    extern void ps2PortPublishPadState(const uint8_t*);
    for(unsigned seq=1;seq<=512;seq++) {
        {std::ofstream command(path);command<<seq<<" 4096 1 128 128\n";}
        ps2PortPublishPadState(neutral.data());
    }
    stop=true;diagnostics.join();fflush(stderr);dup2(saved,STDERR_FILENO);close(saved);
    unsetenv("PS2_PORT_PAD_COMMAND");unsetenv("PS2_PORT_HEADLESS");unlink(path);
    rewind(capture);std::string text;char buffer[4096];size_t count;
    while((count=fread(buffer,1,sizeof(buffer),capture)))text.append(buffer,count);
    fclose(capture);
    const std::regex ack(R"(\[port-lab pad-command\] seq=(\d+) mask=4096 hold_ms=1 lx=128 ly=128\n)");
    std::set<unsigned> sequences;
    for(std::sregex_iterator i(text.begin(),text.end(),ack),end;i!=end;++i)sequences.insert(std::stoul((*i)[1]));
    require(sequences.size()==512,"Concurrent diagnostics split or corrupted controller acknowledgments");
    require(*sequences.begin()==1 && *sequences.rbegin()==512,"Controller ACK sequence missing");
    std::cout<<"PASS: 512 controller acknowledgments remain intact during concurrent diagnostics\n";
#endif
}

int main() {
    try {
        for (auto [key,mask] : {std::pair{KEY_LEFT,0x80},std::pair{KEY_RIGHT,0x20},
                               std::pair{KEY_UP,0x10},std::pair{KEY_DOWN,0x40}}) {
            down[key]=true; presses.push_back(key);
            auto s=read();
            require(buttons(s)==mask,"Arrow direction is reversed or duplicated");
            require(s[6]==128 && s[7]==128,"Arrow also moved analog stick");
            require(buttons(read())==mask,"Held arrow generated a release");
            down[key]=false;
            require(buttons(read())==0,"Arrow release retained a second press");
            // A tap entirely between polls must survive exactly one guest read.
            presses.push_back(key); s=read();
            require(buttons(s)==mask,"Quick arrow tap was lost");
            require(buttons(read())==0,"Quick arrow tap was delivered twice");
        }
        for(auto [key,x,y] : {std::array<int,3>{KEY_A,1,128},std::array<int,3>{KEY_D,255,128},
                             std::array<int,3>{KEY_W,128,1},std::array<int,3>{KEY_S,128,255}}) {
            down[key]=true; presses.push_back(key); auto s=read();
            require(buttons(s)==0,"WASD also published a D-pad press");
            require(s[6]==x && s[7]==y,"WASD stick direction is reversed");
            down[key]=false; s=read();
            require(buttons(s)==0 && s[6]==128 && s[7]==128,"Stick failed to release");
            presses.push_back(key);
            require(buttons(read())==0,"Queued WASD tap leaked into D-pad");
        }
        down[KEY_A]=down[KEY_D]=true;
        require(read()[6]==128,"Opposite stick keys failed to cancel");
        down.fill(false);
        presses.push_back(KEY_F1); read();
        require(ps2PortConsumeControlsToggle(),"Guide event was lost");
        require(!ps2PortConsumeControlsToggle(),"Guide event was delivered twice");
        testConcurrentCommandAcknowledgments();
        std::cout << "PASS: arrow directions, single tap/hold/release, independent WASD stick and guide events\n";
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
