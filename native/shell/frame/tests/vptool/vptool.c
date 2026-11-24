// vptool: drives a wlr virtual pointer (absolute moves, button presses) on the running compositor, for the drag tests.
// usage: vptool W H op...   ops: move X Y | press | release | rclick | sleep MS | drag X0 Y0 X1 Y1 STEPS MS
// drag prints the epoch time in ms when it starts, so a test can measure how long a reaction took
#include <wayland-client.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include "wlr-virtual-pointer-unstable-v1-client-protocol.h"
static struct wl_seat *seat; static struct zwlr_virtual_pointer_manager_v1 *mgr;
static void g(void *d, struct wl_registry *r, uint32_t n, const char *i, uint32_t v){
 if(!strcmp(i,"wl_seat")&&!seat) seat=wl_registry_bind(r,n,&wl_seat_interface,1);
 if(!strcmp(i,"zwlr_virtual_pointer_manager_v1")) mgr=wl_registry_bind(r,n,&zwlr_virtual_pointer_manager_v1_interface,1);}
static void gr(void*d,struct wl_registry*r,uint32_t n){}
static const struct wl_registry_listener rl={g,gr};
int main(int c,char**v){ int W=atoi(v[1]),H=atoi(v[2]);
 struct wl_display*d=wl_display_connect(NULL); struct wl_registry*r=wl_display_get_registry(d); wl_registry_add_listener(r,&rl,0); wl_display_roundtrip(d);
 if(!mgr){fprintf(stderr,"no vp manager\n");return 1;}
 struct zwlr_virtual_pointer_v1*p=zwlr_virtual_pointer_manager_v1_create_virtual_pointer(mgr,seat);
 unsigned t=1000;
 for(int i=3;i<c;i++){
  if(!strcmp(v[i],"move")){zwlr_virtual_pointer_v1_motion_absolute(p,t++,atoi(v[i+1]),atoi(v[i+2]),W,H);zwlr_virtual_pointer_v1_frame(p);i+=2;}
  else if(!strcmp(v[i],"rclick")){zwlr_virtual_pointer_v1_button(p,t++,0x111,1);zwlr_virtual_pointer_v1_frame(p);zwlr_virtual_pointer_v1_button(p,t++,0x111,0);zwlr_virtual_pointer_v1_frame(p);}
  else if(!strcmp(v[i],"press")){zwlr_virtual_pointer_v1_button(p,t++,0x110,1);zwlr_virtual_pointer_v1_frame(p);}
  else if(!strcmp(v[i],"release")){zwlr_virtual_pointer_v1_button(p,t++,0x110,0);zwlr_virtual_pointer_v1_frame(p);}
  else if(!strcmp(v[i],"sleep")){wl_display_roundtrip(d);usleep(atoi(v[i+1])*1000);i++;}
  else if(!strcmp(v[i],"drag")){{struct timespec ts;clock_gettime(CLOCK_REALTIME,&ts);printf("%lld\n",(long long)ts.tv_sec*1000+ts.tv_nsec/1000000);fflush(stdout);}
   int x0=atoi(v[i+1]),y0=atoi(v[i+2]),x1=atoi(v[i+3]),y1=atoi(v[i+4]),n=atoi(v[i+5]),ms=atoi(v[i+6]);
    for(int k=1;k<=n;k++){zwlr_virtual_pointer_v1_motion_absolute(p,t+=10,x0+(x1-x0)*k/n,y0+(y1-y0)*k/n,W,H);zwlr_virtual_pointer_v1_frame(p);wl_display_roundtrip(d);usleep(ms*1000);} i+=6;}
 }
 wl_display_roundtrip(d); return 0;}
