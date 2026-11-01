#pragma once
#include <cmath>
namespace adaptive_scale_rule {
// Source-content observations are scheduling evidence, not display/simulation
// FPS. Require sustained stalls; startup, isolated loads and idle gaps stay put.
struct Rule {
    unsigned need=6,count=0,inRow=0;
    double window=-1.0;
    bool Feed(double seconds,double gap_ms) {
        if(!std::isfinite(seconds)||!std::isfinite(gap_ms)||seconds<20.0||!need)return false;
        if(window<0.0)window=seconds;
        if(seconds<window){window=seconds;count=inRow=0;}
        const double windows=std::floor((seconds-window)/10.0);
        if(windows>=1.0){
            inRow=windows==1.0 && count>=need?inRow+1:0;
            count=0;window+=windows*10.0;
        }
        if(gap_ms>=40.0 && gap_ms<=250.0 && count<need)++count;
        return inRow>=3;
    }
};
}
