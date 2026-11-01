SetFactory("OpenCASCADE");
Box(1) = {0,0,0, 160,25,1.2};
Box(2) = {0,0,1.2, 160,1.2,10};
Box(3) = {0,23.8,1.2, 160,1.2,10};
BooleanUnion{ Volume{1}; Delete; }{ Volume{2,3}; Delete; }
Rotate {{1,0,0},{0,0,0},Pi/2} { Volume{1}; }
Translate {7,-3,40} { Volume{1}; }
Save "turned.step";
