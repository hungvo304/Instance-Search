cd ..
file="../data/processed_data/extracted_faces/links2.txt"
#vid=154
#videos=(12 34 35 37 42 44 46 51 62 74 88 112 115 121 126 127 129 130 131 138 147 160 184 186 195 232)
#videos=(88 112 115 121 126 127 129 130 131 138 147 160 184 186 195 232)
#videos=(12 34 35 46 51 62 74 88 112 115 121 126 127 129 130 131 138 147 160 184 186 195 232)
videos=(130 131 138 147 160 184 186 195 232)
id=-1
while IFS= read -r line
#while : ; do
do
    id=$((id+1))
    vid=${videos[$id]}
    echo "Processing Video ${vid}"
    python process_face_data.py ${vid}

    for batch in $(find ../data/processed_data/extracted_faces/video${vid} -type f -name '*')
    do
	echo "Batch $batch"
	while : ; do
	    output=$(gdrive upload -p ${line} $batch)
	    #output=$(gdrive upload -r -p 1j4erN74Q6lpb6QL5QQ39ZFSXb7zivhpA ../data/processed_data/extracted_faces/video${vid})
	    if [[ $output != *"Error 403"* ]]; then
		echo "\tUpload Successfully!\n"
		break
	    fi
	    echo "\tUpload Failed, Start reuploading\n"
	done

    done

    rm ../data/processed_data/extracted_faces/video${vid}/*
    vid=$((vid+1))
    
done <"$file"
