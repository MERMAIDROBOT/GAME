#include "completion_gate.hpp"
#include <cassert>
#include <set>
struct Field { struct { unsigned long long tick; } boundary; unsigned long long generation; };
int main() {
 LocalGS::CompletionGate<Field> gate;
 std::set<unsigned long long> ready;
 auto check=[&](const Field& f){return ready.count(f.boundary.tick)!=0;};
 auto field=[](unsigned long long t,unsigned long long g=1){return std::make_shared<const Field>(Field{{t},g});};
 auto first=field(1); std::weak_ptr<const Field> lease=first;
 assert(!gate.select(first,check));first.reset();assert(!lease.expired());
 // Every newest field is still pending. The earlier completed field must win.
 assert(!gate.select(field(2),check));ready.insert(1);
 auto shown=gate.select(field(3),check);assert(shown&&shown->boundary.tick==1);
 ready.insert(3);shown=gate.select(field(4),check);assert(shown->boundary.tick==3);
 assert(lease.expired());
 // A ready CPU/pixel frame remains eligible with no extra delay.
 ready.insert(4);shown=gate.select(field(4),check);assert(shown->boundary.tick==4);
 // A reset must not show a previous device generation, even with a lower tick.
 ready.clear();assert(!gate.select(field(1,2),check));ready.insert(1);
 shown=gate.select(field(2,2),check);assert(shown->generation==2&&shown->boundary.tick==1);
 gate.select({},check);shown.reset();
 // Resource ownership is released on explicit reset.
 auto last=field(10,3);std::weak_ptr<const Field> pending=last;
 gate.select(last,check);last.reset();assert(!pending.expired());gate.clear();assert(pending.expired());
}
